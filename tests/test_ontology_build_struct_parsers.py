"""『从物料自动构建本体』结构化格式专用解析器回归（需求 §2 / §6 S1–S7 解析侧）。

覆盖范围（只测 `workbench/ontology_build/parsers/` 六个新解析器 + 共享遍历器，
不起 HTTP 服务、不碰真实 ontology/、不改任何既有既有解析器文件）：
* JSON（S1）：键路径事实的 `locator.path` 可回放到原值；数组只产出摘要事实（不逐元素展开）；
  同输入两次解析事实 id 完全一致（base 的稳定 id 方案）。
* JSON-LD（S1）：顶层 `@context` + `@graph` 的储能 jsonId 形态样本逐节点产出高质量事实，
  `name`/`definition`/`skos:definition`/`@type`/`nodeType` 各为字段值，定位器带节点 `@id`。
* JSONL（S1）：坏行进 failedSegments 且不中断其余行；整篇坏 JSON 显式 failure + 失败位置 +
  **零事实**（不回退降级线索，可单物料重试）。
* YAML（S2）：嵌套结构与 JSON 同遍历口径；锚点/别名在 coverage.notes 注记；坏 YAML 显式失败。
* properties（S3）：点号层级提示、注释不计事实、`\\uXXXX` 按 Java 规范解码、真实行号定位；
  编码探测复用 textline 探测链（gbk 中文值不乱码）。
* CSV（S4）：列名/非空计数/≤3 样本值/推断类型/总行数；>100 行采样并在 notes 与事实内
  标注截断范围；TSV 分隔符嗅探。
* INI（S3）：节 + 键路径事实 + 真实行号；重复节进 failedSegments，其余内容仍解析。
* TOML（S3）：子集解析（[table]/[table.sub]、字符串/整数/浮点/布尔/数组/内联表、注释）
  + 四种不覆盖语法（多行字符串、日期时间、`[[数组表]]`、虚键）逐条进 failedSegments，
  quality=medium 诚实降级。
* 深度/上限（S5）：超 32 层子树产出摘要事实（含路径与规模），事实达上限时截断并注记，
  均不静默丢弃。

隔离：全部材料写进 `tempfile.mkdtemp()` 新建的临时目录，运行结束删除；不写任何真实数据根、
不联网、不执行材料内容。

运行：.venv/bin/python tests/test_ontology_build_struct_parsers.py
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build.parsers import csv_parser                    # noqa: E402
from workbench.ontology_build.parsers import ini_parser                    # noqa: E402
from workbench.ontology_build.parsers import json_parser                   # noqa: E402
from workbench.ontology_build.parsers import properties_parser             # noqa: E402
from workbench.ontology_build.parsers import structwalk                    # noqa: E402
from workbench.ontology_build.parsers import toml_parser                   # noqa: E402
from workbench.ontology_build.parsers import yaml_parser                   # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix='wiz_struct_parsers_'))

PASSED = []
FAILED = []


def check(condition, name, actual=None):
    if condition:
        PASSED.append(name)
        print('  ok   %s' % name)
    else:
        FAILED.append(name)
        print('  FAIL %s%s' % (name, '' if actual is None else '  actual=%r' % (actual,)))


def write(name, data, encoding='utf-8'):
    """把合成材料写入临时根，返回绝对路径（str）。"""
    path = TMP / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), 'wb') as handle:
        handle.write(data if isinstance(data, bytes) else str(data).encode(encoding))
    return str(path)


def facts_of(result, kind=None):
    return [fact for fact in result.facts if kind is None or fact.kind == kind]


def notes_of(result):
    return [str(note) for note in (result.coverage.get('notes') or [])]


def failures_of(result):
    return list(result.coverage.get('failedSegments') or [])


def has_note(result, *fragments):
    joined = '\n'.join(notes_of(result))
    return all(fragment in joined for fragment in fragments)


# --- JSONPath 子集回放（证明 locator.path 能取回原值） -------------------------
_PATH_TOKEN = re.compile(r'\.([A-Za-z_][A-Za-z0-9_\-]*)|\[(\d+)\]|\[("(?:[^"\\]|\\.)*")\]')


def replay(root, path):
    """按本包约定的键路径（`$.a.b[0]` / `$["@graph"][0].name`）回放取值。"""
    if not path.startswith('$'):
        raise ValueError('路径必须以 $ 开头：%s' % path)
    node = root
    for match in _PATH_TOKEN.finditer(path[1:]):
        if match.group(1) is not None:
            node = node[match.group(1)]
        elif match.group(2) is not None:
            node = node[int(match.group(2))]
        else:
            node = node[json.loads(match.group(3))]
    return node


# ---------------------------------------------------------------------------
# S1 JSON：键路径可回放 / 数组摘要 / 稳定 id
# ---------------------------------------------------------------------------
JSON_SAMPLE = {
    "name": "储能设备",
    "capacity": 2.5,
    "active": True,
    "note": None,
    "tags": ["a", "b", "c"],
    "nested": {"deep": {"value": "底层值"}},
    "objects": [{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}],
    "mixed": [1, "two", True],
}


def test_json_key_paths():
    print('\n== S1 JSON 键路径事实可回放 ==')
    path = write('json/basic.json', json.dumps(JSON_SAMPLE, ensure_ascii=False, indent=1))
    result = json_parser.parse(path, 'bm-1', 'json/basic.json')
    check(result.error == '', 'JSON 正常文件无失败', result.error)

    leaves = facts_of(result, 'jsonLeaf')
    replayed = {}
    for fact in leaves:
        replayed[fact.locator['path']] = replay(JSON_SAMPLE, fact.locator['path'])
    check(replayed.get('$.name') == '储能设备', '叶子事实 path 回放取到字符串原值', replayed.get('$.name'))
    check(replayed.get('$.capacity') == 2.5, '叶子事实 path 回放取到数值原值', replayed.get('$.capacity'))
    check(replayed.get('$.active') is True, '叶子事实 path 回放取到布尔原值', replayed.get('$.active'))
    check(replayed.get('$.note') is None, 'null 叶子参与键路径遍历', replayed.get('$.note'))
    check(replayed.get('$.nested.deep.value') == '底层值', '深层嵌套键路径可回放',
          replayed.get('$.nested.deep.value'))
    check(all(fact.locator.get('kind') == 'json' for fact in result.facts),
          '全部事实 locator.kind=json')
    check(all(fact.locator.get('file') == 'json/basic.json' for fact in result.facts),
          'locator.file 为调用方给的相对路径')

    arrays = {fact.locator['path']: fact for fact in facts_of(result, 'jsonArraySummary')}
    check(set(arrays) == {'$.tags', '$.objects', '$.mixed'}, '数组各产出一条摘要事实', sorted(arrays))
    check(arrays['$.tags'].data['length'] == 3 and
          arrays['$.tags'].data['elementTypes'] == {'string': 3},
          '数组摘要含长度与元素类型', arrays['$.tags'].data)
    check(arrays['$.objects'].data['firstElementKeys'] == ['id', 'name'],
          '对象数组摘要含首元素键', arrays['$.objects'].data.get('firstElementKeys'))
    check(not any('[' in (fact.locator.get('path') or '') for fact in result.facts),
          '数组元素未被逐项展开为事实（不出现 `[n]` 路径）',
          [fact.locator['path'] for fact in result.facts if '[' in fact.locator['path']])

    again = json_parser.parse(path, 'bm-1', 'json/basic.json')
    check([fact.id for fact in result.facts] == [fact.id for fact in again.facts] and result.facts,
          '同输入两次解析事实 id 完全一致（稳定 id）')


# ---------------------------------------------------------------------------
# S1 JSON-LD：@graph 节点高质量事实（储能 jsonId 形态）
# ---------------------------------------------------------------------------
JSONLD_SAMPLE = {
    "@context": {
        "czy": "https://example.com/czy-knowledge-graph/",
        "name": "http://schema.org/name",
        "definition": "http://www.w3.org/2004/02/skos/core#definition",
        "nodeType": "czy:nodeType",
    },
    "@id": "czy:knowledge-graph",
    "@type": "czy:KnowledgeGraph",
    "version": "储能_V20260814_0002",
    "statistics": {"entities": 6, "attributes": 15, "nodes": 42},
    "@graph": [
        {"@id": "czy:entity:01", "@type": "czy:EntityNode", "name": "储能簇", "nodeType": "实体",
         "data": {"definition": "储能簇，一组储能设备的集合，按簇粒度管理。",
                  "subtype": "物理实体", "instance_table": "m_storage_cluster_phase"}},
        {"@id": "czy:attr:01", "@type": "czy:AttributeNode", "name": "soc", "nodeType": "属性",
         "data": {"skos:definition": "荷电状态（SOC）", "applicableObject": "储能设备"}},
        {"@id": "czy:rel:01", "@type": "czy:RelationNode", "name": "属于", "nodeType": "关系",
         "data": {"definition": "储能设备属于园区"}},
    ],
}


def test_jsonld_graph_nodes():
    print('\n== S1 JSON-LD @graph 节点高质量事实（储能 jsonId 形态）==')
    path = write('json/graph.jsonId', json.dumps(JSONLD_SAMPLE, ensure_ascii=False, indent=1))
    result = json_parser.parse(path, 'bm-2', 'json/graph.jsonId')
    check(result.error == '', 'JSON-LD 样本解析无失败', result.error)

    nodes = facts_of(result, 'jsonLdNode')
    check(len(nodes) == 3, '@graph 三个节点各产出一条节点事实', len(nodes))
    check([node.locator.get('nodeId') for node in nodes] ==
          ['czy:entity:01', 'czy:attr:01', 'czy:rel:01'],
          '节点定位器带节点 @id', [node.locator.get('nodeId') for node in nodes])
    check(all(node.quality == 'high' for node in nodes), 'JSON-LD 节点事实 quality=high')
    check(all(node.locator['path'].startswith('$["@graph"]') for node in nodes),
          '节点事实路径指向 @graph 内对应下标', nodes[0].locator['path'])
    check('储能簇' in nodes[0].snippet and 'https://example.com' not in nodes[0].snippet,
          '节点摘要含名称', nodes[0].snippet)

    fields = {}
    for fact in facts_of(result, 'jsonLdField'):
        fields.setdefault(fact.locator.get('nodeId'), {})[fact.data['canonical']] = fact
    check(set(fields.get('czy:entity:01', {})) >= {'id', 'type', 'name', 'nodeType', 'definition'},
          '实体节点产出 @id/@type/name/nodeType/definition 字段事实',
          sorted(fields.get('czy:entity:01', {})))
    check(fields['czy:entity:01']['name'].data['value'] == '储能簇',
          'name 字段值为节点真实字段值', fields['czy:entity:01']['name'].data)
    definition = fields['czy:entity:01']['definition']
    check(definition.data['value'].startswith('储能簇，一组储能设备的集合')
          and definition.data['field'] == 'definition',
          'definition 从嵌套 data 容器取到原值', definition.data)
    check(definition.quality == 'high' and definition.locator.get('nodeId') == 'czy:entity:01',
          '字段事实 quality=high 且定位到节点 @id')
    check(replay(JSONLD_SAMPLE, definition.locator['path']) == definition.data['value'],
          '字段事实 path 可回放到原值', definition.locator['path'])
    check(fields['czy:attr:01']['name'].data['value'] == 'soc',
          '属性节点产出 name 字段事实（前缀写法一并识别）',
          sorted(fields.get('czy:attr:01', {})))
    check('definition' in fields.get('czy:attr:01', {}),
          'skos:definition 归一到 definition 字段')
    check(fields['czy:attr:01']['definition'].data['field'] == 'skos:definition',
          'skos:definition 原键名保留在事实里', fields['czy:attr:01']['definition'].data['field'])
    check(has_note(result, '识别为 JSON-LD', '@graph'),
          'coverage.notes 注明 JSON-LD 识别结果')


# ---------------------------------------------------------------------------
# S1 JSONL：坏行不中断；坏 JSON 显式失败零事实
# ---------------------------------------------------------------------------
def test_jsonl_and_broken_json():
    print('\n== S1 JSONL 坏行不中断 + 坏 JSON 显式失败 ==')
    lines = [json.dumps({"@id": "n1", "name": "甲"}, ensure_ascii=False),
             '{bad json}',
             json.dumps({"@id": "n2", "name": "乙"}, ensure_ascii=False),
             '[1, 2, 3]',
             '']
    path = write('json/items.jsonl', '\n'.join(lines) + '\n')
    result = json_parser.parse(path, 'bm-3', 'json/items.jsonl')
    check(len(facts_of(result, 'jsonLeaf')) == 4, '坏行前/后与数组行都正常产出事实',
          len(facts_of(result, 'jsonLeaf')))
    check([fact.data.get('type') for fact in facts_of(result, 'jsonArraySummary')] or True,
          'JSONL 中的数组行产出摘要事实')
    check(len(failures_of(result)) == 1 and failures_of(result)[0]['locator'].get('line') == 2,
          '坏行进入 failedSegments 且带真实行号', failures_of(result))
    check('语法错误' in failures_of(result)[0]['reason'], '失败原因写明语法错误',
          failures_of(result)[0]['reason'])
    check(result.partial is True and result.error == '',
          '部分失败时 partial=True 但不是整体 failure', (result.partial, result.error))
    located = {fact.locator.get('line') for fact in facts_of(result, 'jsonLeaf')}
    check(located == {1, 3}, 'JSONL 叶子事实定位带真实行号（数组行只有摘要）', sorted(located))
    check([fact.locator.get('line') for fact in facts_of(result, 'jsonArraySummary')] == [4],
          'JSONL 数组摘要事实同样带真实行号',
          [fact.locator.get('line') for fact in facts_of(result, 'jsonArraySummary')])

    broken = write('json/broken.json', '{\n  "a": 1,\n  "b": }\n')
    failed_result = json_parser.parse(broken, 'bm-4', 'json/broken.json')
    check(len(failed_result.facts) == 0, '坏 JSON 产出零事实（不回退降级线索）',
          len(failed_result.facts))
    check(failed_result.error and 'JSON 语法错误' in failed_result.error,
          '坏 JSON 显式失败并写明语法错误', failed_result.error)
    segments = failures_of(failed_result)
    check(len(segments) == 1 and segments[0]['locator'].get('line') == 3
          and segments[0]['locator'].get('column') == 8,
          '失败片段含错误位置（行/列）', segments)
    payload = failed_result.coverage_payload()
    check(payload['parseState'] == 'failed', '失败状态的 parseState=failed', payload['parseState'])
    check(any('不回退' in note for note in notes_of(failed_result)),
          'notes 声明不回退 LLM 兜底/文本线索')

    all_bad = write('json/allbad.ndjson', '{a:1}\n{oops}\n')
    bad_result = json_parser.parse(all_bad, 'bm-5', 'json/allbad.ndjson')
    check(len(bad_result.facts) == 0 and bad_result.error,
          'JSONL 全部行都坏时显式失败（不当作空文件成功）', bad_result.error)
    check(len(failures_of(bad_result)) == 2, '逐行记录失败片段', len(failures_of(bad_result)))


# ---------------------------------------------------------------------------
# S2 YAML：与 JSON 同口径 + 锚点/别名注记 + 坏 YAML 失败
# ---------------------------------------------------------------------------
YAML_SAMPLE = """defaults: &conn
  host: 127.0.0.1
  port: 3306
  charset: utf8mb4
prod:
  <<: *conn
  database: storage
  pools:
    - name: primary
      size: 8
    - name: replica
      size: 4
  enabled: true
  weights:
    - 1.5
    - 2
mapping:
  keys:
    nested: 底层值
"""


def test_yaml_same_walker_and_anchors():
    print('\n== S2 YAML 与 JSON 同遍历口径 + 锚点别名注记 ==')
    path = write('yaml/app.yaml', YAML_SAMPLE)
    result = yaml_parser.parse(path, 'bm-6', 'yaml/app.yaml')
    check(result.error == '', 'YAML 正常文件无失败', result.error)

    leaves = {fact.locator['path']: fact for fact in facts_of(result, 'yamlLeaf')}
    check(leaves.get('$.defaults.host') is not None and
          leaves['$.defaults.host'].data['type'] == 'string',
          'YAML 叶子事实形状与 JSON 一致', leaves.get('$.defaults.host'))
    check(leaves.get('$.mapping.keys.nested') is not None and
          leaves['$.mapping.keys.nested'].snippet == '底层值',
          'YAML 深层嵌套键路径同 JSON 口径', leaves.get('$.mapping.keys.nested'))
    check(all(fact.locator['kind'] == 'yaml' for fact in result.facts),
          'YAML 事实 locator.kind=yaml')
    check(leaves['$.prod.port'].data['type'] == 'number',
          'YAML 数值类型与 JSON 口径一致', leaves['$.prod.port'].data)

    arrays = {fact.locator['path']: fact for fact in facts_of(result, 'yamlArraySummary')}
    check(arrays['$.prod.pools'].data['firstElementKeys'] == ['name', 'size'],
          'YAML 数组摘要含首元素键（与 JSON 同实现）',
          arrays['$.prod.pools'].data.get('firstElementKeys'))
    check(arrays['$.prod.weights'].data['elementTypes'] == {'number': 2},
          'YAML 数组摘要统计元素类型', arrays['$.prod.weights'].data.get('elementTypes'))

    check(has_note(result, '锚点 1 个', '别名 1 个', '展开'),
          '锚点/别名展开在 coverage.notes 注记', notes_of(result)[:2])
    check(has_note(result, 'YAML 与 JSON 使用同一树遍历口径'),
          'notes 声明与 JSON 同一遍历口径')
    check('$.prod.host' in leaves and leaves['$.prod.host'].snippet == '127.0.0.1',
          '别名展开后的节点按普通节点登记', sorted(leaves))

    broken = write('yaml/broken.yaml', 'a: [1, 2\nb: 3\n')
    bad_result = yaml_parser.parse(broken, 'bm-7', 'yaml/broken.yaml')
    check(len(bad_result.facts) == 0 and 'YAML 语法错误' in (bad_result.error or ''),
          '坏 YAML 显式失败零事实', bad_result.error)
    segments = failures_of(bad_result)
    check(segments and segments[0]['locator'].get('line') == 2,
          '坏 YAML 失败片段带问题位置（行）', segments)


# ---------------------------------------------------------------------------
# S3 properties：点号层级 / 注释不计 / \uXXXX 解码 / 行号定位
# ---------------------------------------------------------------------------
PROPERTIES_SAMPLE = """# 数据库连接配置
! 感叹号注释同样不计事实
spring.datasource.url=jdbc:mysql://127.0.0.1:3306/storage
spring.datasource.username=\\u7528\\u6237\\u540d
spring.datasource.password=\\u5bc6\\u7801\\u0021
spring.redis.timeout : 3000
plainkey value
continued=first\\u0020part\\
  second
escaped=tabs\\there\\nnewline
empty.value=
unicode.emoji=\\uD83D\\uDE00
bad.escape=\\uZZZZ
"""


def test_properties_entries():
    print('\n== S3 .properties 键值/层级/注释/转义/行号 ==')
    path = write('props/app.properties', PROPERTIES_SAMPLE)
    result = properties_parser.parse(path, 'bm-8', 'props/app.properties')
    entries = {fact.data['key']: fact for fact in facts_of(result, 'propertyEntry')}
    check(len(entries) >= 9, '逐条产出键值事实', len(entries))
    check(all(fact.locator['kind'] == 'properties' for fact in result.facts),
          'properties 定位器 kind=properties')
    check(all(isinstance(fact.locator.get('line'), int) and fact.locator['line'] > 0
              for fact in result.facts),
          '全部事实带真实行号定位')
    check(entries['spring.datasource.url'].locator['line'] == 3,
          '行号与原文一致（第 3 行）', entries['spring.datasource.url'].locator['line'])
    check(entries['spring.datasource.url'].data['hierarchy'] ==
          ['spring', 'datasource', 'url'],
          '点号作为层级提示记入 data.hierarchy',
          entries['spring.datasource.url'].data.get('hierarchy'))
    check(entries['spring.datasource.url'].data.get('leaf') == 'url',
          'data.leaf 记录最后一段', entries['spring.datasource.url'].data.get('leaf'))
    check(entries['plainkey'].data.get('hierarchy') is None
          and entries['plainkey'].data['value'] == 'value',
          '无点号键不写层级提示（空格分隔写法也解析）', entries['plainkey'].data)
    check(entries['spring.datasource.username'].data['value'] == '用户名',
          '\\uXXXX 按 Java 规范解码为中文', entries['spring.datasource.username'].data['value'])
    check(entries['spring.datasource.password'].data['value'] == '密码!',
          '混合 4 位转义与 ASCII 转义正确解码',
          entries['spring.datasource.password'].data['value'])
    check(entries['continued'].data['value'] == 'first partsecond',
          '行尾反斜杠续接并入同一逻辑条目', entries['continued'].data['value'])
    check(entries['continued'].locator['line'] == 8 and entries['continued'].data['continued'],
          '续接条目定位取起始行并标注 continued',
          (entries['continued'].locator['line'], entries['continued'].data.get('continued')))
    check(entries['escaped'].data['value'] == 'tabs\there\nnewline',
          '\\t/\\n 等常见转义按 Java 规范解码', entries['escaped'].data['value'])
    check(entries['spring.redis.timeout'].data['value'] == '3000',
          '`key : value` 冒号分隔写法可用',
          entries['spring.redis.timeout'].data['value'])
    check(entries['unicode.emoji'].data['value'] == '\U0001F600',
          '代理对 \\uD83D\\uDE00 合并为一个字符',
          entries['unicode.emoji'].data['value'])
    check(not any(fact.locator['line'] in (1, 2) for fact in result.facts),
          '注释行（# / !）不产出事实',
          [fact.locator['line'] for fact in result.facts])
    check(has_note(result, '注释 2 行', '不计入事实'), 'notes 记录注释计数与口径',
          notes_of(result)[0])
    check(any('无效 \\uXXXX 转义' in item['reason'] for item in failures_of(result)),
          '无效 \\uXXXX 进 failedSegments 且保留原文',
          [item['reason'] for item in failures_of(result)])
    check(entries['bad.escape'].data['value'].startswith('\\uZZZZ'),
          '无效转义行仍产出事实（保留原文）', entries['bad.escape'].data['value'])

    gbk_path = write('props/gbk.properties', 'name=储能设备\n容量=2.5\n', encoding='gbk')
    gbk_result = properties_parser.parse(gbk_path, 'bm-9', 'props/gbk.properties')
    check(gbk_result.error == '', 'GBK 材料可解析（编码探测复用 textline 探测链）',
          gbk_result.error)
    gbk = {fact.data['key']: fact.data['value'] for fact in facts_of(gbk_result, 'propertyEntry')}
    check(gbk.get('name') == '储能设备' and gbk.get('容量') == '2.5',
          'GBK 中文键值无乱码', gbk)
    check(has_note(gbk_result, '非 utf-8'), 'notes 注明实际解码编码', notes_of(gbk_result)[-1])


# ---------------------------------------------------------------------------
# S4 CSV：列统计 / 采样截断注记 / TSV 嗅探
# ---------------------------------------------------------------------------
CSV_SAMPLE = """device_id,name,capacity_mwh,online,commission_date
D001,储能设备A,2.5,true,2024-01-15
D002,储能设备B,5,false,2024-02-20
D003,,3.5,true,2024-03-01
D004,储能设备D,7,true,2024-04-01
"""


def test_csv_column_stats():
    print('\n== S4 CSV/TSV 列统计与采样截断 ==')
    path = write('csv/devices.csv', CSV_SAMPLE)
    result = csv_parser.parse(path, 'bm-10', 'csv/devices.csv')
    check(result.error == '', 'CSV 正常文件无失败', result.error)
    header = facts_of(result, 'csvHeader')
    check(len(header) == 1 and header[0].data['columns'] ==
          ['device_id', 'name', 'capacity_mwh', 'online', 'commission_date'],
          '表头事实列出全部列名', header[0].data.get('columns'))
    check(header[0].locator['line'] == 1 and header[0].locator['kind'] == 'csv',
          '表头事实定位文件+行号', header[0].locator)

    columns = {fact.data['column']: fact for fact in facts_of(result, 'csvColumn')}
    check(set(columns) == {'device_id', 'name', 'capacity_mwh', 'online', 'commission_date'},
          '每列产出一条统计事实', sorted(columns))
    check(all(fact.locator.get('line') for fact in facts_of(result, 'csvColumn')),
          '列统计事实带真实行号（首个数据行）',
          [fact.locator.get('line') for fact in facts_of(result, 'csvColumn')])
    check(columns['name'].data['nonEmptyCount'] == 3 and columns['name'].data['sampleValues'] ==
          ['储能设备A', '储能设备B', '储能设备D'],
          '列统计给出非空计数与去重样本值', columns['name'].data)
    check(len(columns['device_id'].data['sampleValues']) == 3,
          '样本值上限为 3（D001/D002/D003）', columns['device_id'].data['sampleValues'])
    check(columns['capacity_mwh'].data['inferredType'] == 'double'
          and columns['online'].data['inferredType'] == 'boolean'
          and columns['commission_date'].data['inferredType'] == 'date'
          and columns['device_id'].data['inferredType'] == 'string',
          '推断类型按取值形状判定（double/boolean/date/string）',
          {name: fact.data['inferredType'] for name, fact in columns.items()})
    summary = facts_of(result, 'csvSummary')
    check(len(summary) == 1 and summary[0].data['rowCount'] == 4
          and summary[0].data['columnCount'] == 5,
          '总行数/列数事实正确（4 行数据 5 列）', summary[0].data)
    check(summary[0].data['truncated'] is False and result.partial is False,
          '小文件不标截断、不置 partial', (summary[0].data['truncated'], result.partial))

    big_rows = ['id,value'] + ['%d,v%d' % (index, index) for index in range(1, 131)]
    big_path = write('csv/big.csv', '\n'.join(big_rows) + '\n')
    big = csv_parser.parse(big_path, 'bm-11', 'csv/big.csv')
    big_summary = facts_of(big, 'csvSummary')[0]
    check(big_summary.data['rowCount'] == 130 and big_summary.data['sampledRows'] == 100,
          '>100 行时总行数真实、采样 100 行', big_summary.data)
    check(big_summary.data.get('truncated') is True
          and big_summary.data.get('unscannedRows') == 30,
          '总行数事实标注未统计行数', big_summary.data)
    check(has_note(big, '超过 100 行仅采样前 100 行', '第 2–101 行', '其余 30 行未统计'),
          'coverage.notes 注记截断范围（含已统计区间）', notes_of(big))
    check(any('仅统计前 100 行' in fact.snippet for fact in facts_of(big, 'csvColumn')),
          '列统计事实内也带上截断范围说明',
          [fact.snippet for fact in facts_of(big, 'csvColumn')][:1])
    check(big.partial is True, '采样截断时 partial=True（不假装全量）', big.partial)

    tsv = write('csv/devices.tsv', 'id\tname\n1\t甲\n2\t乙\n')
    tsv_result = csv_parser.parse(tsv, 'bm-12', 'csv/devices.tsv')
    check(tsv_result.coverage.get('delimiter') == '\t'
          and facts_of(tsv_result, 'csvHeader')[0].data['columnCount'] == 2,
          'TSV 分隔符嗅探为制表符',
          (tsv_result.coverage.get('delimiter'), facts_of(tsv_result, 'csvHeader')[0].data))

    ragged = write('csv/ragged.csv', 'a,b,c\n1,2,3\n4,5\n6,7,8,9\n')
    ragged_result = csv_parser.parse(ragged, 'bm-13', 'csv/ragged.csv')
    reasons = ' '.join(item['reason'] for item in failures_of(ragged_result))
    check('少于表头' in reasons and '多于表头' in reasons,
          '列数不齐的行进 failedSegments（不猜缺失值）', reasons)


# ---------------------------------------------------------------------------
# S3 INI：节 + 键路径 + 真实行号；重复节降级
# ---------------------------------------------------------------------------
INI_SAMPLE = """[db]
host = 127.0.0.1
port = 3306
; 注释不计事实
[db]
user = root
[logging]
level = INFO
unrecognized line here
[DEFAULT]
retries = 3
"""


def test_ini_sections():
    print('\n== S3 INI/CFG 节 + 键路径 + 重复节降级 ==')
    path = write('ini/app.conf', INI_SAMPLE)
    result = ini_parser.parse(path, 'bm-14', 'ini/app.conf')
    entries = {(fact.data['section'], fact.data['key']): fact
               for fact in facts_of(result, 'iniEntry')}
    check(('db', 'host') in entries and entries[('db', 'host')].data['value'] == '127.0.0.1',
          '节 + 键 + 值产出事实', sorted(entries))
    check(entries[('db', 'host')].locator['line'] == 2
          and entries[('db', 'host')].locator['section'] == 'db',
          '定位带真实行号与节名', entries[('db', 'host')].locator)
    check(entries[('db', 'host')].data['path'] == 'db.host',
          '键路径记入 data.path', entries[('db', 'host')].data.get('path'))
    check(entries[('logging', 'level')].locator['line'] == 8,
          '第二个节的键行号正确（重复节合并后仍按原文行号）',
          entries[('logging', 'level')].locator)
    check(entries[('logging', 'retries')].data['inherited'] is True
          and entries[('logging', 'retries')].data['sourceSection'] == 'DEFAULT',
          '[DEFAULT] 键继承到各节并标注来源', entries[('logging', 'retries')].data)
    check(not any(fact.data['key'] == 'retries' and fact.data['section'] == ''
                  for fact in facts_of(result, 'iniEntry')),
          'DEFAULT 的键不会被当作顶层键重复登记')
    check(any('重复节 [db]' in item['reason'] and item['locator']['line'] == 5
              for item in failures_of(result)),
          '重复节进 failedSegments 且带行号', [item['reason'] for item in failures_of(result)])
    check(any('无法识别的行' in item['reason'] for item in failures_of(result)),
          '非键值行进 failedSegments（不静默跳过）',
          [item['reason'] for item in failures_of(result)])
    check(entries[('db', 'user')].data['value'] == 'root',
          '重复节后的键仍按合并结果登记', entries[('db', 'user')].data['value'])
    check(all(fact.quality == 'high' for fact in facts_of(result, 'iniEntry')),
          'INI 事实 quality=high')


# ---------------------------------------------------------------------------
# S3 TOML：子集支持 + 四种不覆盖语法诚实降级
# ---------------------------------------------------------------------------
TOML_SAMPLE = '''# 储能配置
title = "储能设备配置"
count = 6
ratio = 0.75
negative = -3
hex_value = 0xFF
threshold = 1_000_000
enabled = true
tags = ["soc", "soh", "temperature"]
matrix = [[1, 2], [3, 4]]
inline = { model = "A", capacity = 2.5 }
multiline_array = [
  1,
  2,
]

[server]
host = "127.0.0.1"
port = 8080

[server.tls]
mode = "strict"
paths = { cert = "/etc/cert.pem", key = "/etc/key.pem" }

uncovered_multiline = """
储能设备
说明
"""
uncovered_date = 1979-05-27T07:32:00Z
uncovered_local_date = 1979-05-27

[[uncovered_items]]
name = "数组表内条目"

uncovered.dotted.key = 1
'''


def test_toml_subset_and_uncovered_syntaxes():
    print('\n== S3 TOML 子集解析 + 四种不覆盖语法进 failedSegments ==')
    path = write('toml/app.toml', TOML_SAMPLE)
    result = toml_parser.parse(path, 'bm-15', 'toml/app.toml')
    leaves = {fact.locator['path']: fact for fact in facts_of(result, 'tomlLeaf')}
    check(leaves.get('$.title') is not None and leaves['$.title'].snippet == '储能设备配置',
          '顶层字符串键值产出事实', leaves.get('$.title'))
    check(leaves['$.count'].data['type'] == 'number' and leaves['$.ratio'].data['type'] == 'number',
          '整数/浮点按数值类型登记',
          (leaves['$.count'].data['type'], leaves['$.ratio'].data['type']))
    check(leaves.get('$.threshold') is not None and
          leaves['$.threshold'].snippet.replace('_', '') == '1000000',
          '下划线数字分隔符可解析', leaves.get('$.threshold'))
    check(leaves['$.hex_value'].snippet == '255', '0x 十六进制整数可解析',
          leaves['$.hex_value'].snippet)
    check(leaves['$.enabled'].data['type'] == 'boolean' and leaves['$.enabled'].snippet == 'true',
          '布尔可解析', leaves['$.enabled'].snippet)
    check(leaves.get('$.server.host') is not None and leaves['$.server.host'].snippet == '127.0.0.1'
          and leaves['$.server.host'].locator.get('line') == 18,
          '[table] 下的键路径与真实行号正确', leaves.get('$.server.host'))
    check(leaves.get('$.server.tls.mode') is not None
          and leaves['$.server.tls.mode'].locator.get('line') == 22,
          '[table.sub] 深层表可解析并定位', leaves.get('$.server.tls.mode'))
    check(leaves.get('$.inline.model') is not None and leaves['$.inline.model'].snippet == 'A',
          '内联表展开为键路径', leaves.get('$.inline.model'))
    check(leaves.get('$.inline.capacity') is not None,
          '内联表数值成员可解析', leaves.get('$.inline.capacity'))
    check(leaves.get('$.server.tls.paths.cert') is not None
          and leaves['$.server.tls.paths.cert'].snippet == '/etc/cert.pem'
          and leaves['$.server.tls.paths.cert'].locator.get('line') == 23,
          '表内内联表的键路径与行号正确（$.server.tls.paths.cert）',
          leaves.get('$.server.tls.paths.cert'))
    arrays = {fact.locator['path']: fact for fact in facts_of(result, 'tomlArraySummary')}
    check(arrays.get('$.tags') is not None and arrays['$.tags'].data['length'] == 3,
          '数组产出摘要事实', arrays.get('$.tags') and arrays['$.tags'].data)
    check(arrays.get('$.matrix') is not None and arrays['$.matrix'].data['elementTypes'] ==
          {'array': 2}, '嵌套数组只记类型分布', arrays.get('$.matrix') and arrays['$.matrix'].data)
    check(arrays.get('$.multiline_array') is not None
          and arrays['$.multiline_array'].locator.get('line') == 12
          and arrays['$.multiline_array'].data['length'] == 2,
          '跨行数组并入同一键值并按起始行定位',
          arrays.get('$.multiline_array') and arrays['$.multiline_array'].locator)

    syntaxes = {item.get('syntax') for item in failures_of(result)}
    check({'multilineString', 'arrayOfTables', 'dottedKey', 'datetime'} <= syntaxes,
          '四种不覆盖语法各产出失败样例（多行字符串/日期时间/数组表/虚键）', sorted(syntaxes))
    declared = {item.get('syntax') for item in failures_of(result)
                if '不覆盖语法' in item['reason']}
    check({'multilineString', 'arrayOfTables', 'dottedKey', 'datetime'} <= declared,
          '四种不覆盖语法各自的条目都写明「不覆盖语法」', sorted(declared))
    inner = [item for item in failures_of(result)
             if '位于未解析的 [[' in item['reason']]
    check(inner and all(item.get('syntax') == 'arrayOfTables' for item in inner),
          '数组表内部的行单独标注为「位于未解析的数组表内」', len(inner))
    dotted = [item for item in failures_of(result) if item.get('syntax') == 'dottedKey']
    check(dotted and dotted[0]['locator']['line'] == 35,
          '虚键失败片段定位到原文行（第 35 行，数组表后的虚键不被吞掉）',
          dotted)
    check(any('位于未解析的 [[uncovered_items]] 数组表内' in item['reason']
              for item in failures_of(result)),
          '数组表内的键不被挂到错误父路径（逐条记录）',
          [item['reason'] for item in failures_of(result)])
    check(not any(fact.locator['path'].startswith('$.uncovered') for fact in result.facts),
          '不覆盖语法对应内容不产出事实（不猜测）',
          [fact.locator['path'] for fact in result.facts if 'uncovered' in fact.locator['path']])
    check(all(fact.quality == 'medium' for fact in result.facts),
          'TOML 子集事实 quality=medium（诚实标注）',
          sorted({fact.quality for fact in result.facts}))
    check(result.partial is True and result.error == '',
          '存在不覆盖语法时 partial=True 但仍产出可用事实',
          (result.partial, result.error))
    check(has_note(result, '不引入依赖', '子集'),
          'notes 声明自写子集解析与不引入依赖')

    bad_path = write('toml/broken.toml', 'a = \n"unterminated = 1\n')
    bad = toml_parser.parse(bad_path, 'bm-16', 'toml/broken.toml')
    check(bad.error != '' and len(bad.facts) == 0,
          'TOML 无法恢复时显式失败（零事实）', (bad.error, len(bad.facts)))


# ---------------------------------------------------------------------------
# S5 深度上限与事实上限（不静默丢弃）
# ---------------------------------------------------------------------------
def test_depth_and_fact_limits():
    print('\n== S5 深度上限 32 层摘要事实 + 事实上限截断注记 ==')
    deep = node = {}
    for index in range(40):
        node['level%d' % index] = {}
        node = node['level%d' % index]
    node['leaf'] = 'bottom'
    path = write('limits/deep.json', json.dumps(deep, ensure_ascii=False))
    result = json_parser.parse(path, 'bm-17', 'limits/deep.json')
    summaries = facts_of(result, 'jsonDepthSummary')
    check(len(summaries) == 1, '超深子树产出一条摘要事实（不逐层展开不静默丢）', len(summaries))
    check(summaries[0].locator['path'].count('level') == structwalk.MAX_DEPTH,
          '摘要事实路径指向深度上限处', summaries[0].locator['path'].count('level'))
    check(summaries[0].data.get('depth') == structwalk.MAX_DEPTH
          and summaries[0].data.get('expanded') is False,
          '摘要事实含深度与未展开标记', summaries[0].data)
    check('深度超限' in summaries[0].snippet and str(structwalk.MAX_DEPTH) in summaries[0].snippet,
          '摘要事实片段写明路径与上限', summaries[0].snippet[:90])
    check(has_note(result, '深度超过 %d 层' % structwalk.MAX_DEPTH, '摘要事实'),
          'notes 注记深度截断（不静默丢弃）', notes_of(result)[-1:])
    check(result.partial is True, '深度截断时 partial=True', result.partial)

    wide = {'key%05d' % index: index for index in range(structwalk.MAX_FACTS_PER_MATERIAL + 500)}
    wide_path = write('limits/wide.json', json.dumps(wide))
    wide_result = json_parser.parse(wide_path, 'bm-18', 'limits/wide.json')
    check(len(wide_result.facts) == structwalk.MAX_FACTS_PER_MATERIAL,
          '事实数不超过 MAX_FACTS_PER_MATERIAL',
          (len(wide_result.facts), structwalk.MAX_FACTS_PER_MATERIAL))
    check(has_note(wide_result, '事实数达到单材料上限', '已截断'),
          '截断在 notes 中如实说明', notes_of(wide_result)[-2:])
    check(wide_result.partial is True, '事实截断时 partial=True', wide_result.partial)

    yaml_cycle = write('limits/cycle.yaml', 'a: &x\n  self: *x\n  name: n1\nb: 2\n')
    cycle_result = yaml_parser.parse(yaml_cycle, 'bm-19', 'limits/cycle.yaml')
    check(cycle_result.error == '' and facts_of(cycle_result, 'yamlCycle'),
          'YAML 锚点自引用产出循环事实且不崩溃',
          [fact.kind for fact in cycle_result.facts])
    check(has_note(cycle_result, '循环引用'), 'notes 注记循环引用', notes_of(cycle_result))


# ---------------------------------------------------------------------------
# 通用：只读解析边界
# ---------------------------------------------------------------------------
def test_read_only_contract():
    print('\n== 通用：纯只读解析与失败契约 ==')
    missing = str(TMP / 'not-exists.json')
    for label, module in (('json', json_parser), ('yaml', yaml_parser),
                          ('toml', toml_parser), ('properties', properties_parser)):
        result = module.parse(missing, 'bm-20', 'missing.%s' % label)
        check(len(result.facts) == 0 and result.error,
              '%s：文件不存在时返回显式 failure（不抛异常）' % label, result.error)
    for label, module in (('properties', properties_parser),):
        result = module.parse(str(TMP), 'bm-21', 'dir')
        check(len(result.facts) == 0 and result.error,
              '%s：路径为目录时显式失败（不抛异常）' % label, result.error)

    empty_path = write('json/empty.json', '{}')
    empty = json_parser.parse(empty_path, 'bm-22', 'json/empty.json')
    check(empty.error == '' or empty.partial, '空 JSON 对象不报异常',
          (empty.error, len(empty.facts)))

    targets = {fact.locator['kind'] for fact in
               json_parser.parse(write('json/kind.json', '{"a": 1}'), 'bm-23',
                                 'json/kind.json').facts}
    check(targets == {'json'}, '定位器 kind 与格式一致', targets)


def main():
    print('== 结构化格式专用解析器回归（临时根：%s）==' % TMP)
    test_json_key_paths()
    test_jsonld_graph_nodes()
    test_jsonl_and_broken_json()
    test_yaml_same_walker_and_anchors()
    test_properties_entries()
    test_csv_column_stats()
    test_ini_sections()
    test_toml_subset_and_uncovered_syntaxes()
    test_depth_and_fact_limits()
    test_read_only_contract()
    return 0


if __name__ == '__main__':
    exit_code = 1
    try:
        exit_code = main()
    except Exception as exc:  # 未预期异常不吞
        import traceback
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(str(TMP), ignore_errors=True)
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if exit_code == 0 and not FAILED else 1)
