# -*- coding: utf-8 -*-
"""D11：三类合成材料与人工金样格式回归（tests/fixtures/ontology_token_pilot）。

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§11 样本①②③、§8 D11 行。
覆盖点（任务书 6 项测试要求）：
1. manifest 登记的 sha256/bytes 与实际文件逐项一致（防篡改/防漂移），
   且 manifest 覆盖目录内除自身外的全部文件（内容 hash 独立登记，不漏登记）。
2. 三类样本可完整解析：simple（JSON-LD @graph + 嵌套 JSON）、amplified（60 实例）
   均为合法 JSON/JSON-LD；schema_conflict 的 .sql 为文本片段（DDL/规则注释），
   文件头注明由材料解析器按 ddl/text 类事实处理（定位器形态见接口文档 08 §1.3），
   不要求 JSON 解析。
3. 金样格式校验：每条期望含 key/type/name；property 必带 ownerKey；fields 为子集断言
   （dict）；同名异主体 mustNotMerge 组内 key 互异且都出现在期望清单；
   expectedConflicts 每条含 field 与两侧来源（source/expect）说明；跨文件关系
   targetIdentity 必须真实出现在被引文件原文中。
4. 晚出现结构可判定：amplified 前半（第 1–40 台）实例确实没有金样 structure 声明的
   lateFields，后半（第 41–60 台）实例全部携带；二级放大字段、稀有字段（独占实例）、
   单位差异（kW/W 形态互斥集合）、异常枚举值逐一程序化断言。
5. 规模声明：amplified 序列化 ≥40KB；simple JSON-LD 在 6–12KB 区间——
   这是**合成对标**（对标“原 8KB JSON/JSON-LD”量级），不是 D18 原始真实文件；
   原文件若后续提供须另行登记 hash。
6. 无真实数据泄漏：samples/ 与 golden/ 全量扫描不含 'sk-'、'api_key' 等密钥样式串、
   不含真实域名（http/https 仅允许保留示例域 example.org）、不含邮箱样式串。

金样键是语义身份键空间（如 battery_ess.rated_power），不要求与模型批内临时 key 字面
一致；factId 由解析决定、候选键由生成与对齐决定——本测试只锁 fixture 自身质量，
不评价生成结果（生成评测归 D13/D18）。

隔离纪律：纯文件断言，不 import workbench、不启服务、不写仓库其他位置、
不碰真实 ontology/ 与 data/；fixtures 由确定性生成器产出并以 manifest 锁内容。

运行：python3 tests/run.py --test tests/test_ontology_token_pilot_fixtures.py
（或直接 python3 tests/test_ontology_token_pilot_fixtures.py）
"""
import hashlib
import json
import re
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))   # 仓库根入 sys.path（本测试为纯文件断言，无需 import workbench）

FIXTURE = REPO / 'tests' / 'fixtures' / 'ontology_token_pilot'

CANDIDATE_TYPES = ('object', 'property', 'link', 'rule', 'action')
# 06 分册 §1.6：property→dataType 枚举
DATA_TYPES = ('text', 'number', 'boolean', 'dateTime', 'array', 'struct', 'timeSeries')

FORBIDDEN_MARKERS = ('sk-', 'api_key', 'apikey', 'AKIA', 'PRIVATE KEY', 'password',
                     'passwd', 'secret', 'Bearer ', 'bearer ')

PASSED = []
FAILED = []
SEQ = [0]


def check(cond, message):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    return False


def load_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 第 1 节：manifest 与实际文件一致性
# ---------------------------------------------------------------------------

def section_manifest():
    print('\n----- manifest hash 独立登记与防漂移 -----')
    manifest = load_json(FIXTURE / 'manifest.json')
    if not check(manifest is not None, 'manifest.json 可解析为 JSON'):
        return
    check(manifest.get('manifestVersion') == 1, 'manifest.manifestVersion == 1')
    check('D11' in str(manifest.get('task') or ''), 'manifest.task 标注 D11')
    declaration = str(manifest.get('syntheticDeclaration') or '')
    check('合成' in declaration and '不含' in declaration,
          'manifest 携带合成声明（合成数据、不含真实业务数据/密钥）')
    check('合成对标' in str(manifest.get('scaleNote') or ''),
          'manifest.scaleNote 写明规模为合成对标（非 D18 原始文件）')

    entries = manifest.get('files')
    if not check(isinstance(entries, list) and len(entries) >= 10,
                 'manifest.files 为非空清单（≥10 项）'):
        return
    ok_hash = ok_bytes = ok_field = True
    for entry in entries:
        rel = str(entry.get('path') or '')
        if (not rel or rel.startswith('/') or '..' in rel.split('/')
                or not (FIXTURE / rel).is_file()):
            ok_field = False
            print('  [失败] 路径非法或文件缺失: %r' % rel)
            continue
        data = (FIXTURE / rel).read_bytes()
        if entry.get('sha256') != hashlib.sha256(data).hexdigest():
            ok_hash = False
            print('  [失败] sha256 不一致: %s' % rel)
        if entry.get('bytes') != len(data):
            ok_bytes = False
            print('  [失败] bytes 不一致: %s' % rel)
        if not str(entry.get('purpose') or '').strip() or entry.get('synthetic') is not True:
            ok_field = False
            print('  [失败] 缺用途说明或合成标记: %s' % rel)
    check(ok_hash, 'manifest 全部 sha256 与实际文件一致（防篡改/防漂移）')
    check(ok_bytes, 'manifest 全部 bytes 与实际文件一致')
    check(ok_field, 'manifest 每项登记路径/用途说明/合成标记完整')

    actual = {p.relative_to(FIXTURE).as_posix() for p in FIXTURE.rglob('*')
              if p.is_file() and p.name != 'manifest.json'}
    registered = {str(entry.get('path') or '') for entry in entries}
    check(actual == registered,
          'manifest 覆盖目录内除自身外全部文件（实际 %d = 登记 %d，无漏登/多登）'
          % (len(actual), len(registered)))


# ---------------------------------------------------------------------------
# 第 2 节：三类样本可解析 + 解析边界声明
# ---------------------------------------------------------------------------

def section_samples_parse():
    print('\n----- 三类样本完整解析 -----')
    topo = FIXTURE / 'samples' / 'simple' / 'station_topology.jsonld'
    doc = load_json(topo)
    if check(doc is not None, '样本①-a station_topology.jsonld 可被 json.loads 完整解析'):
        graph = doc.get('@graph') if isinstance(doc, dict) else None
        check(isinstance(graph, list) and len(graph) >= 5,
              'JSON-LD 含 @graph 且 ≥5 个节点（对象+链接关系）')
        check(all(isinstance(n, dict) and str(n.get('@id') or '') for n in (graph or [])),
              '每个 @graph 节点带 @id（解析器 nodeId 定位输入完整）')
    gw = FIXTURE / 'samples' / 'simple' / 'gateway_config.json'
    cfg = load_json(gw)
    if check(cfg is not None, '样本①-b gateway_config.json 可被 json.loads 完整解析'):
        gateway = cfg.get('gateway') if isinstance(cfg, dict) else None
        has_nested = any(isinstance(v, dict) for v in (gateway or {}).values())
        has_array = any(isinstance(v, list) for v in (gateway or {}).values())
        check(has_nested and has_array, 'gateway 配置同时含嵌套对象与数组')

    amp = FIXTURE / 'samples' / 'amplified' / 'device_instances.json'
    data = load_json(amp)
    if check(data is not None, '样本② amplified/device_instances.json 可被 json.loads 完整解析'):
        devices = data.get('devices') if isinstance(data, dict) else None
        nos = [d.get('deviceNo') for d in (devices or []) if isinstance(d, dict)]
        check(isinstance(devices, list) and len(devices) == 60,
              '重复实例共 60 个（同模式逐级放大）')
        check(len(set(nos)) == 60, '60 个 deviceNo 互不重复')

    inv = load_json(FIXTURE / 'samples' / 'schema_conflict' / 'inventory_a.json')
    met = load_json(FIXTURE / 'samples' / 'schema_conflict' / 'metering_b.json')
    check(inv is not None and isinstance((inv or {}).get('inventory'), dict),
          '样本③ inventory_a.json 可被 json.loads 完整解析')
    check(met is not None and isinstance((met or {}).get('metering'), dict),
          '样本③ metering_b.json 可被 json.loads 完整解析')

    sql_path = FIXTURE / 'samples' / 'schema_conflict' / 'dispatch_logic.sql'
    ok = check(sql_path.is_file() and sql_path.stat().st_size > 0,
               '样本③ dispatch_logic.sql 文本片段存在且非空')
    if ok:
        head = sql_path.read_text(encoding='utf-8')[:800]
        check('材料解析器' in head and 'ddl' in head,
              '文本片段头部注明由材料解析器按 ddl/text 类事实处理（不要求 JSON 解析）')
        check("{kind:'ddl',file,line,table,column}" in head,
              '头部注明定位器形态（接口文档 08 §1.3 的 ddl locator）')
        check('合成' in head, '文本片段头部带合成声明')

    check((FIXTURE / 'samples' / 'simple' / 'station_topology.jsonld').is_file()
          and (FIXTURE / 'samples' / 'amplified').is_dir()
          and (FIXTURE / 'samples' / 'schema_conflict').is_dir(),
          '三类样本目录结构齐全（simple/amplified/schema_conflict）')


# ---------------------------------------------------------------------------
# 第 3 节：金样格式校验
# ---------------------------------------------------------------------------

def golden_files():
    return sorted((FIXTURE / 'golden').glob('*.json'))


def validate_golden(path):
    name = path.relative_to(FIXTURE).as_posix()
    golden = load_json(path)
    if not check(golden is not None, '%s 可解析为 JSON' % name):
        return
    check(golden.get('schemaVersion') == 1, '%s schemaVersion == 1' % name)
    check(golden.get('synthetic') is True, '%s 声明 synthetic' % name)

    golden_for = golden.get('goldenFor')
    ok_for = (isinstance(golden_for, list) and golden_for
              and all((FIXTURE / str(p)).is_file() and '..' not in str(p).split('/')
                      for p in golden_for))
    check(ok_for, '%s goldenFor 指向真实样本文件' % name)

    candidates = golden.get('expectedCandidates')
    if not check(isinstance(candidates, list) and candidates,
                 '%s expectedCandidates 为非空清单' % name):
        return
    keys = []
    ok_basic = ok_owner = ok_evidence = True
    for item in candidates:
        key, ctype, cname = item.get('key'), item.get('type'), item.get('name')
        if not (isinstance(key, str) and key.strip()
                and ctype in CANDIDATE_TYPES
                and isinstance(cname, str) and cname.strip()):
            ok_basic = False
            print('  [失败] 期望项缺 key/type/name 或 type 非法: %r' % (key,))
        if ctype == 'property' and not (isinstance(item.get('ownerKey'), str)
                                        and item['ownerKey'].strip()):
            ok_owner = False
            print('  [失败] property 期望缺 ownerKey: %r' % (key,))
        fields = item.get('fields')
        if not isinstance(fields, dict):
            ok_basic = False
            print('  [失败] fields 非子集断言 dict: %r' % (key,))
        else:
            for field_name in fields:
                if (ctype == 'property' and field_name == 'dataType'
                        and fields[field_name] not in DATA_TYPES):
                    ok_basic = False
                    print('  [失败] dataType 非法: %r -> %r' % (key, fields[field_name]))
        ev = item.get('evidence')
        if not (isinstance(ev, dict) and str(ev.get('file') or '').strip()
                and str(ev.get('identity') or '').strip()
                and str(ev.get('locatorKind') or '').strip()):
            ok_evidence = False
            print('  [失败] 证据身份说明不完整（file/identity/locatorKind）: %r' % (key,))
        keys.append(key)
    check(ok_basic, '%s 每条期望含 key/type/name 且 type/dataType 合法（fields 为子集断言）'
                    % name)
    check(ok_owner, '%s property 期望全部携带 ownerKey（语义归属）' % name)
    check(ok_evidence, '%s 每条期望带证据身份说明（语义身份，不绑定 factId）' % name)
    check(len(set(keys)) == len(keys), '%s 期望键在金样内唯一（%d 条）' % (name, len(keys)))

    key_set = set(keys)
    groups = golden.get('mustNotMerge') or []
    ok_group = True
    for group in groups:
        gkeys = group.get('keys') or []
        if (not isinstance(gkeys, list) or len(gkeys) < 2
                or len(set(gkeys)) != len(gkeys)
                or not set(gkeys).issubset(key_set)
                or not str(group.get('reason') or '').strip()):
            ok_group = False
            print('  [失败] mustNotMerge 组不合法: %r' % (gkeys,))
    check(ok_group, '%s mustNotMerge 组内 key 互异（≥2）且都在期望清单、带判定理由' % name)

    conflicts = golden.get('expectedConflicts') or []
    ok_conflict = True
    for conflict in conflicts:
        sides = conflict.get('sides') or []
        if (not str(conflict.get('field') or '').strip()
                or not str(conflict.get('candidateKey') or '').strip()
                or not isinstance(sides, list) or len(sides) < 2
                or not all(str(side.get('source') or '').strip()
                           and str(side.get('expect') or '').strip()
                           and (FIXTURE / str(side.get('source'))).is_file()
                           for side in sides)):
            ok_conflict = False
            print('  [失败] expectedConflicts 条目不合法: %r' % (conflict.get('id'),))
    check(ok_conflict, '%s expectedConflicts 每条含 field 与两侧来源（source/expect，'
                       '指向真实文件）' % name)

    min_counts = golden.get('minimumCounts')
    ok_counts = isinstance(min_counts, dict) and bool(min_counts) and \
        all(t in CANDIDATE_TYPES and isinstance(n, int) and n > 0
            for t, n in min_counts.items())
    if ok_counts:
        counted = {}
        for item in candidates:
            counted[item['type']] = counted.get(item['type'], 0) + 1
        ok_counts = all(counted.get(t, 0) >= n for t, n in min_counts.items())
    check(ok_counts, '%s minimumCounts 类型合法且期望清单覆盖下限' % name)

    relations = golden.get('crossFileRelations') or []
    ok_rel = True
    for relation in relations:
        to_file = FIXTURE / str(relation.get('toFile') or '')
        target = str(relation.get('targetIdentity') or '')
        if (str(relation.get('fromKey') or '') not in key_set
                or str(relation.get('toKey') or '') not in key_set
                or str(relation.get('expectCandidateKey') or '') not in key_set
                or not to_file.is_file() or not target):
            ok_rel = False
            continue
        if target not in to_file.read_text(encoding='utf-8'):
            ok_rel = False
            print('  [失败] 跨文件关系目标 %r 未出现在 %s 原文' % (target, relation['toFile']))
    check(ok_rel, '%s crossFileRelations 指向真实文件且目标身份真实出现在被引文件' % name)


def section_golden_format():
    print('\n----- 金样格式校验 -----')
    files = golden_files()
    if not check(len(files) == 4, 'golden/ 共 4 份金样（simple×2 + amplified + schema_conflict）'):
        return
    for path in files:
        validate_golden(path)
    amp = load_json(FIXTURE / 'golden' / 'amplified_device_instances.golden.json')
    structure = (amp or {}).get('structure')
    ok_structure = isinstance(structure, dict) and all([
        isinstance(structure.get('instanceCount'), int) and structure['instanceCount'] > 0,
        isinstance(structure.get('lateFields'), list) and structure['lateFields'],
        isinstance(structure.get('lateFieldsFirstIndex'), int),
        isinstance(structure.get('rareFields'), list) and structure['rareFields'],
        isinstance(structure.get('unitVariants'), list) and structure['unitVariants'],
        isinstance(structure.get('abnormalEnumValues'), list) and structure['abnormalEnumValues'],
        isinstance(structure.get('minSerializedBytes'), int),
    ])
    check(ok_structure, 'amplified 金样 structure 块字段齐全（晚出现/稀有/单位/异常枚举可判定）')
    sc = load_json(FIXTURE / 'golden' / 'schema_conflict.golden.json')
    check(bool((sc or {}).get('mustNotMerge')) and bool((sc or {}).get('expectedConflicts'))
          and bool((sc or {}).get('crossFileRelations')),
          'schema_conflict 金样同时标注 mustNotMerge / expectedConflicts / 跨文件关系')


# ---------------------------------------------------------------------------
# 第 4 节：晚出现结构 / 稀有字段 / 单位差异 / 异常枚举可判定
# ---------------------------------------------------------------------------

def section_amplified_structure():
    print('\n----- amplified 晚出现结构与稀有结构程序化断言 -----')
    golden = load_json(FIXTURE / 'golden' / 'amplified_device_instances.golden.json')
    data = load_json(FIXTURE / 'samples' / 'amplified' / 'device_instances.json')
    if golden is None or data is None:
        check(False, 'amplified 样本/金样可加载（前置失败）')
        return
    structure = golden.get('structure') or {}
    devices = data.get('devices') or []
    check(len(devices) == structure.get('instanceCount'),
          '实例数与金样 structure.instanceCount 一致（%d）' % len(devices))

    split = int(structure.get('lateFieldsFirstIndex') or 1) - 1
    first_half, latter_half = devices[:split], devices[split:]
    for field in structure.get('lateFields') or []:
        check(all(field not in d for d in first_half)
              and all(field in d for d in latter_half),
              '晚出现字段 %s：前半 %d 台均无、后半 %d 台均有（晚出现结构可判定）'
              % (field, len(first_half), len(latter_half)))
    tier2_from = int(structure.get('tier2FromIndex') or 1) - 1
    for field in structure.get('tier2Fields') or []:
        check(all(field not in d for d in devices[:tier2_from])
              and all(field in d for d in devices[tier2_from:]),
              '二级放大字段 %s：自第 %d 台起才出现（逐级放大）' % (field, tier2_from + 1))

    for rare in structure.get('rareFields') or []:
        field = rare.get('field')
        holders = [d.get('deviceNo') for d in devices if field in d]
        check(holders == [rare.get('deviceNo')],
              '稀有字段 %s 仅由 %s 独有（未扩散到其他实例）' % (field, rare.get('deviceNo')))

    for variant in structure.get('unitVariants') or []:
        base, alt = variant.get('field'), variant.get('variantField')
        base_holders = {d.get('deviceNo') for d in devices if base in d}
        alt_holders = {d.get('deviceNo') for d in devices if alt in d}
        declared = set(variant.get('variantDeviceNos') or [])
        check(len(base_holders) + len(alt_holders) == len(devices)
              and alt_holders == declared and not (base_holders & alt_holders),
              '单位差异 %s/%s：两种形态互斥且 variant 恰为 %s（kW vs W 保留）'
              % (base, alt, sorted(declared)))

    abnormal = structure.get('abnormalEnumValues') or []
    normal = set(structure.get('normalStatusValues') or [])
    ok_enum = True
    for item in abnormal:
        field, value, device_no = item.get('field'), item.get('value'), item.get('deviceNo')
        holder = next((d for d in devices if d.get('deviceNo') == device_no), None)
        if holder is None or holder.get(field) != value:
            ok_enum = False
            print('  [失败] 异常枚举缺失: %s=%s（%s）' % (field, value, device_no))
    for device in devices:
        if device.get('status') not in (normal | {item.get('value') for item in abnormal}):
            ok_enum = False
            print('  [失败] 出现未登记的状态值: %r' % device.get('status'))
    check(ok_enum, '异常枚举值逐一在位（UNKNOWN_3/MAINTENANCE_HOLD），其余状态均在登记集合内')


# ---------------------------------------------------------------------------
# 第 5 节：规模声明（合成对标）
# ---------------------------------------------------------------------------

def section_scale():
    print('\n----- 规模声明（合成对标，非原 8KB 真实文件） -----')
    topo = FIXTURE / 'samples' / 'simple' / 'station_topology.jsonld'
    gw = FIXTURE / 'samples' / 'simple' / 'gateway_config.json'
    amp = FIXTURE / 'samples' / 'amplified' / 'device_instances.json'
    topo_size = topo.stat().st_size
    amp_size = amp.stat().st_size
    check(6 * 1024 <= topo_size <= 12 * 1024,
          'simple JSON-LD 序列化 %dB 在 6–12KB 区间（合成对标原 8KB 量级）' % topo_size)
    check(3 * 1024 <= gw.stat().st_size <= 6 * 1024,
          'simple 配置序列化 %dB 在 3–6KB 区间（合成对标约 4KB）' % gw.stat().st_size)
    golden = load_json(FIXTURE / 'golden' / 'amplified_device_instances.golden.json')
    declared = int(((golden or {}).get('structure') or {}).get('minSerializedBytes') or 40960)
    check(amp_size >= declared and declared >= 40 * 1024,
          'amplified 序列化 %dB ≥ 金样声明下限 %dB（≥40KB）' % (amp_size, declared))
    readme = (FIXTURE / 'README.md').read_text(encoding='utf-8')
    check('合成对标' in readme and '8KB' in readme,
          'README 写明样本①为合成对标原 8KB 规模、不是 D18 原始真实文件')
    check('40KB' in readme and '解析器' in readme,
          'README 写明 amplified 规模下限与 .sql 文本片段的解析器处理口径')


# ---------------------------------------------------------------------------
# 第 6 节：无真实数据泄漏
# ---------------------------------------------------------------------------

def section_leakage():
    print('\n----- 泄漏扫描（samples/ + golden/） -----')
    targets = sorted(list((FIXTURE / 'samples').rglob('*'))
                     + list((FIXTURE / 'golden').rglob('*')))
    targets = [p for p in targets if p.is_file()]
    url_hosts = set()
    bad_marker = []
    bad_host = []
    email_like = []
    tld_like = []
    for path in targets:
        text = path.read_text(encoding='utf-8')
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                bad_marker.append('%s: %r' % (path.name, marker))
        for host in re.findall(r'https?://([A-Za-z0-9.-]+)', text):
            url_hosts.add(host)
            if host != 'example.org':
                bad_host.append('%s: %s' % (path.name, host))
        for hit in re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', text):
            email_like.append('%s: %s' % (path.name, hit))
        for hit in re.findall(r'\.(?:com|cn|net)(?![A-Za-z])', text):
            tld_like.append('%s: %s' % (path.name, hit))
    check(len(targets) == 10, '扫描范围覆盖 samples/ 与 golden/ 全部 %d 个文件' % len(targets))
    check(not bad_marker, '无密钥样式串（sk-/api_key/AKIA/password/secret 等）: %s' % bad_marker)
    check(not bad_host and url_hosts <= {'example.org'},
          'http(s) 仅允许保留示例域 example.org（实际: %s）' % sorted(url_hosts))
    check(not email_like, '无邮箱样式串: %s' % email_like)
    check(not tld_like, '无 .com/.cn/.net 真实域名样式: %s' % tld_like)


# ---------------------------------------------------------------------------

def main():
    print('========== D11 三类合成材料与人工金样格式回归 ==========')
    print('fixture: %s' % FIXTURE)
    if not check(FIXTURE.is_dir(), 'fixture 目录存在: tests/fixtures/ontology_token_pilot'):
        return 1
    section_manifest()
    section_samples_parse()
    section_golden_format()
    section_amplified_structure()
    section_scale()
    section_leakage()
    return 0


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Exception as exc:
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if code == 0 and not FAILED else 1)
