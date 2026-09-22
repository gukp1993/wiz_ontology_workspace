"""跨批临时键命名空间化回归（R1：跨批同名 key 导致属性宿主错误/同名属性误并）。

缺陷回顾（2026-09-22，/tmp/repro_r1.py 复现）：SYSTEM_EXTRACT 只要求模型 key
「批内唯一」，不同批次完全可以重用同名键（obj/p）；pipeline 逐批把校验通过的
候选累加进跨批集合后 alignment.align 全局对齐，属性的对齐键含宿主对象名，而
宿主经引用索引由 ownerKey 解析——同名键「首到者胜」让第二批判定的属性解析到
第一批判定的宿主，同名同 dataType 的两个属性被误并为一项（alignedKey 错记）。

修复（两层）：
1. 硬保证：pipeline 在每批 verify 之后、入累积集合（与同事务落库）之前调用
   `alignment.namespace_batch(verified, position, existing=accumulated)`，与累积
   集合**实际冲突**的候选 key 加 `b{position}:` 前缀并同步重写批内 ownerKey /
   链接 sourceRef/targetRef——跨批键绝不冲突，批内引用关系完整保留（截断拆批
   的子批同属一个 position）；无冲突的键保留原键，入库行为与历史一致（旧候选行、
   既有按原始键断言的用例天然兼容）。
2. 兜底：`alignment._ref_index` 保留同名键的全部命中（按出现顺序），
   `_owner_name_for` 对多命中按「属性之前最近的宿主」消歧——未命名空间化的
   旧存量候选行（origin.key 无前缀，不迁移）与直调 align 的场景不再必然解析错。

断言覆盖：
A. 纯函数层：前缀与批内引用重写（全量模式）、两批同 key 属性不同宿主、两批同
   key 对象同名（合并且 mergedFromKeys 正确）、链接端点跨批重写、拆批子批同
   前缀、幂等、空键不前缀、冲突条件模式（无冲突保留原键/冲突键连同引用重写/
   目标键占用兜底）、旧格式候选（无前缀）对齐兼容（邻近消歧 + 既有 D08 语义
   不变）、llm 输出 key 规范化。
B. 管线集成层：两批模型输出同名 key（obj/p），批 1 无冲突保留原始键、批 2 冲突
   键命名空间化为 b2:*，生成完成后两条「额定功率」各自宿主电池/逆变器
   （alignedKey 正确），ownerKey/origin.key 与入库键一致。

隔离（AGENTS.md 测试隔离铁律）：集成场景独立临时根（WIZ_WORKBENCH_ROOT +
该根下 WIZ_DATABASE_URL），替换 llm.extract_candidates 控制模型响应（不访问
网络、不连真实库、不依赖其它测试文件）；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_key_namespace.py
"""
import os
import shutil
import sys
import tempfile
import threading
import traceback
from pathlib import Path

os.environ['WIZ_BUILD_LLM_CONCURRENCY'] = '1'   # 串行抽取：批次落库顺序确定（import 前设置）

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import storage  # noqa: E402
from workbench.ontology_build import alignment, llm, pipeline, protocol, runner  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

protocol.LLM_CONCURRENCY = 1   # 双保险：调度器在调用点读模块属性

UID = 'key-namespace-owner'
JOIN_SECONDS = 60
PROVIDER = {'endpoint': 'mock', 'model': 'mock'}
USAGE = {'calls': 1, 'promptBytes': 16, 'completionBytes': 8, 'durationMs': 1}

PASSED = []
FAILED = []
SEQ = [0]
ROOTS = []


def _short(value, limit=400):
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - 摘要展示绝不影响断言流程
        text = str(value)
    return text if len(text) <= limit else text[:limit] + '…'


def check(cond, message, actual=None, expected=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if expected is not None:
        print('  预期: ' + _short(expected))
    if actual is not None:
        print('  实际: ' + _short(actual))
    return False


# --- 候选构造（模拟模型输出的最小结构，evidence 指向真实 fact id 的位置由调用方保证） ---

def obj(key, name, fact_id):
    return {'key': key, 'type': 'object', 'name': name, 'definition': '%s定义' % name,
            'fields': {}, 'ownerKey': '',
            'evidence': {'_record': [fact_id]}, 'evidenceStatus': 'supported', 'conflicts': []}


def prop(key, name, owner_key, fact_id, data_type='number'):
    return {'key': key, 'type': 'property', 'name': name, 'definition': '%s定义' % name,
            'fields': {'dataType': data_type}, 'ownerKey': owner_key,
            'evidence': {'definition': [fact_id]}, 'evidenceStatus': 'supported',
            'conflicts': []}


def link(key, name, source_ref, target_ref, fact_id):
    return {'key': key, 'type': 'link', 'name': name, 'definition': '%s定义' % name,
            'fields': {'sourceRef': source_ref, 'targetRef': target_ref,
                       'cardinality': {'source': 'one', 'target': 'many'}},
            'ownerKey': '', 'evidence': {'definition': [fact_id]},
            'evidenceStatus': 'supported', 'conflicts': []}


# --- A. 纯函数层 --------------------------------------------------------------------

def a1_prefix_and_owner_rewrite():
    tag = 'A1 前缀与批内 ownerKey 重写'
    batch = [obj('obj', '电池', 'f-1'), prop('p', '额定功率', 'obj', 'f-1')]
    snapshot = [dict(item) for item in batch]
    out = alignment.namespace_batch(batch, 3)
    check([item['key'] for item in out] == ['b3:obj', 'b3:p'],
          '%s：批内全部候选 key 加 b{position}: 前缀' % tag,
          actual=[item['key'] for item in out], expected=['b3:obj', 'b3:p'])
    check(out[1]['ownerKey'] == 'b3:obj',
          '%s：属性 ownerKey 同步重写到本批新键（批内引用关系保留）' % tag,
          actual=out[1]['ownerKey'], expected='b3:obj')
    check(batch == snapshot,
          '%s：入参列表不被修改（返回新列表）' % tag)


def a1e_two_batches_same_key_different_hosts():
    tag = 'A1e 两批同 key 属性不同宿主（R1 主场景·命名空间化路径）'
    b1 = [obj('obj', '电池', 'f-1'), prop('p', '额定功率', 'obj', 'f-1')]
    b2 = [obj('obj', '逆变器', 'f-2'), prop('p', '额定功率', 'obj', 'f-2')]
    aligned = alignment.align(alignment.namespace_batch(b1, 1) + alignment.namespace_batch(b2, 2))
    props = [c for c in aligned['candidates'] if c['type'] == 'property']
    hosts = sorted(str(c.get('alignedKey')) for c in props)
    check(len(props) == 2,
          '%s：两个不同对象的同名属性不再误并（对齐后仍为 2 条属性）' % tag,
          actual=len(props), expected=2)
    # sorted() 按码点序：电池(U+7535…) 在 逆变器(U+9006…) 之前
    check(hosts == ['property:额定功率#number@电池', 'property:额定功率#number@逆变器'],
          '%s：alignedKey 宿主分别为电池/逆变器（不再错记同一宿主）' % tag,
          actual=hosts,
          expected=['property:额定功率#number@电池', 'property:额定功率#number@逆变器'])
    by_host = {c['alignedKey'].split('@')[1]: c for c in props}
    check(by_host.get('电池', {}).get('key') == 'b1:p'
          and by_host.get('逆变器', {}).get('key') == 'b2:p',
          '%s：每条属性仍指向自己批次的宿主键（b1:p→电池、b2:p→逆变器）' % tag,
          actual={k: v.get('key') for k, v in by_host.items()},
          expected={'电池': 'b1:p', '逆变器': 'b2:p'})


def a1f_same_name_objects_merge():
    tag = 'A1f 两批同 key 对象同名（合并且 mergedFromKeys 正确）'
    b1 = [obj('obj', '电池', 'f-1'), prop('p', '额定功率', 'obj', 'f-1')]
    b2 = [obj('obj', '电池', 'f-2'), prop('p', '标称电压', 'obj', 'f-2')]
    aligned = alignment.align(alignment.namespace_batch(b1, 1) + alignment.namespace_batch(b2, 2))
    objs = [c for c in aligned['candidates'] if c['type'] == 'object']
    check(len(objs) == 1,
          '%s：同名同类型对象跨批仍正常合并（跨批合并语义不因命名空间化改变）' % tag,
          actual=len(objs), expected=1)
    merged = objs[0] if objs else {}
    check(merged.get('key') == 'b1:obj' and merged.get('mergedFromKeys') == ['b2:obj'],
          '%s：合并主项取首到批次键，mergedFromKeys 记录后续批次命名空间化键' % tag,
          actual={'key': merged.get('key'), 'mergedFromKeys': merged.get('mergedFromKeys')},
          expected={'key': 'b1:obj', 'mergedFromKeys': ['b2:obj']})
    props = sorted(c.get('alignedKey') for c in aligned['candidates'] if c['type'] == 'property')
    check(props == ['property:标称电压#number@电池', 'property:额定功率#number@电池'],
          '%s：同宿主不同名属性各自保留（证据并集语义不受影响）' % tag, actual=props)


def a1g_link_refs_rewritten():
    tag = 'A1g 批内链接端点引用跨批重写'
    b1 = [obj('a', '电池', 'f-1'), obj('b', '逆变器', 'f-1'),
          link('l', '供电', 'a', 'b', 'f-1')]
    b2 = [obj('a', '电网', 'f-2'), obj('b', '变压器', 'f-2'),
          link('l', '供电', 'a', 'b', 'f-2')]
    out = alignment.namespace_batch(b1, 1) + alignment.namespace_batch(b2, 2)
    links = [c for c in out if c['type'] == 'link']
    check([(c['fields'].get('sourceRef'), c['fields'].get('targetRef')) for c in links]
          == [('b1:a', 'b1:b'), ('b2:a', 'b2:b')],
          '%s：两批同名链接的 sourceRef/targetRef 各自重写到本批键（不跨批串线）' % tag,
          actual=[(c['fields'].get('sourceRef'), c['fields'].get('targetRef')) for c in links],
          expected=[('b1:a', 'b1:b'), ('b2:a', 'b2:b')])
    dangling = alignment.namespace_batch(
        [{'key': 'x', 'type': 'link', 'name': '悬空', 'definition': 'd',
          'fields': {'sourceRef': 'nowhere', 'targetRef': ''}, 'ownerKey': 'ghost',
          'evidence': {}, 'evidenceStatus': 'inferred', 'conflicts': []}], 4)
    node = dangling[0]
    check(node['fields'].get('sourceRef') == 'nowhere' and node['fields'].get('targetRef') == ''
          and node['ownerKey'] == 'ghost',
          '%s：批外/悬空引用与空值原样保留（不做跨批猜测改写）' % tag, actual=node)


def a1h_split_halves_share_prefix():
    tag = 'A1h 拆批子批命名空间（同 position 同前缀）'
    left = [obj('obj', '电池', 'f-1'), prop('p', '额定功率', 'obj', 'f-1')]
    right = [obj('obj', '逆变器', 'f-2'), prop('p', '额定功率', 'obj', 'f-2')]
    merged = alignment.namespace_batch(left + right, 5)   # 拆批产物合并后同属 position=5
    check([item['key'] for item in merged] == ['b5:obj', 'b5:p', 'b5:obj', 'b5:p'],
          '%s：两个子批候选统一使用同一批次前缀 b5:' % tag,
          actual=[item['key'] for item in merged])
    check(merged[1]['ownerKey'] == 'b5:obj' and merged[3]['ownerKey'] == 'b5:obj',
          '%s：子批属性 ownerKey 同前缀重写' % tag)
    aligned = alignment.align(merged)
    check(len([c for c in aligned['candidates'] if c['type'] == 'property']) == 2,
          '%s：拆批子批内同名属性在批次键空间内仍可按宿主区分' % tag)


def a1i_idempotent_and_empty():
    tag = 'A1i 幂等与空键'
    batch = [{'key': 'b7:obj', 'type': 'object', 'name': '电池', 'definition': 'd',
              'fields': {}, 'ownerKey': '', 'evidence': {}, 'evidenceStatus': 'inferred',
              'conflicts': []},
             {'key': '', 'type': 'object', 'name': '未命名', 'definition': 'd',
              'fields': {}, 'ownerKey': '', 'evidence': {}, 'evidenceStatus': 'insufficient',
              'conflicts': []}]
    out = alignment.namespace_batch(batch, 7)
    check(out[0]['key'] == 'b7:obj' and out[1]['key'] == '',
          '%s：已带本批前缀的键不重复前缀（幂等）；空键不前缀（交 _dedupe_keys 编号）' % tag,
          actual=[out[0]['key'], out[1]['key']], expected=['b7:obj', ''])


def a1l_conditional_namespace():
    tag = 'A1l 冲突条件模式（existing 提供时只为实际冲突的键命名空间化）'
    accumulated = [obj('obj', '电池', 'f-1'), prop('p', '额定功率', 'obj', 'f-1')]
    # 无冲突：键全部保留原样（入库行为与历史一致，旧候选行/既有断言天然兼容）
    clean = [obj('grid', '电网', 'f-2'), prop('v', '电压', 'grid', 'f-2')]
    out = alignment.namespace_batch(clean, 2, existing=accumulated)
    check([item['key'] for item in out] == ['grid', 'v'] and out[1]['ownerKey'] == 'grid',
          '%s：跨批键无冲突时保留原键、引用不改写（兼容路径）' % tag,
          actual=[(item['key'], item['ownerKey']) for item in out],
          expected=[('grid', 'grid'), ('v', 'grid')])
    # 冲突：冲突键连同批内引用一起重写；非冲突键原样保留
    clashing = [obj('obj', '逆变器', 'f-2'), prop('p2', '额定功率', 'obj', 'f-2'),
                prop('w', '电压', 'grid', 'f-2')]
    out = alignment.namespace_batch(clashing, 2, existing=accumulated + clean)
    check([item['key'] for item in out] == ['b2:obj', 'p2', 'w'],
          '%s：仅与累积集合冲突的键加 b{position}: 前缀（b2:obj），其余键不动' % tag,
          actual=[item['key'] for item in out], expected=['b2:obj', 'p2', 'w'])
    check(out[1]['ownerKey'] == 'b2:obj',
          '%s：冲突键的批内引用同步重写（ownerKey → b2:obj）' % tag,
          actual=out[1]['ownerKey'], expected='b2:obj')
    # 目标键占用兜底：b2:obj 已被占用时追加下划线，绝不制造新冲突
    occupied = accumulated + [obj('b2:obj', '占用名', 'f-1')]
    out = alignment.namespace_batch([obj('obj', '逆变器', 'f-2')], 2, existing=occupied)
    check(out[0]['key'] == 'b2:obj_',
          '%s：前缀目标键仍被占用时追加下划线兜底（跨批键绝不冲突的不变量）' % tag,
          actual=out[0]['key'], expected='b2:obj_')


def a1j_legacy_candidates_compatible():
    tag = 'A1j 旧格式候选（无前缀）对齐兼容'
    # 复现脚本原场景：未命名空间化的旧存量行直入 align——邻近消歧兜底解析宿主
    b1 = [obj('obj', '电池', 'f-1'), prop('p', '额定功率', 'obj', 'f-1')]
    b2 = [obj('obj', '逆变器', 'f-2'), prop('p', '额定功率', 'obj', 'f-2')]
    aligned = alignment.align(b1 + b2)
    props = [c for c in aligned['candidates'] if c['type'] == 'property']
    check(len(props) == 2
          and sorted(c.get('alignedKey') for c in props)
          == ['property:额定功率#number@电池', 'property:额定功率#number@逆变器'],
          '%s：同名键旧数据经「最近 precedent」消歧，两条属性宿主各归其位' % tag,
          actual=[(len(props), [c.get('alignedKey') for c in props])])

    def keyed(ckey, cid, name):
        return {'id': cid, 'key': ckey, 'type': 'object', 'name': name, 'definition': 'd',
                'fields': {}, 'ownerKey': '', 'evidence': {'_record': ['f']},
                'evidenceStatus': 'supported', 'conflicts': []}

    def keyed_prop(ckey, name, owner, data_type='number'):
        return {'id': ckey, 'key': ckey, 'type': 'property', 'name': name, 'definition': 'd',
                'fields': {'dataType': data_type}, 'ownerKey': owner,
                'evidence': {'definition': ['f']}, 'evidenceStatus': 'supported',
                'conflicts': []}

    mixed = alignment.align([keyed('oa', 'i1', '设备'), keyed('ob', 'i2', '储能簇'),
                             keyed_prop('p1', '容量', 'oa'), keyed_prop('p2', '容量', 'i1')])
    merged_props = [c for c in mixed['candidates'] if c['type'] == 'property']
    check(len(mixed['candidates']) == 3 and len(merged_props) == 1
          and merged_props[0].get('alignedKey') == 'property:容量#number@设备',
          '%s：键唯一时行为与既有 D08 完全一致（ownerKey 用键或对象 ID 都解析、同名同宿主合并）' % tag,
          actual=(len(mixed['candidates']), [c.get('alignedKey') for c in merged_props]))


def a1k_llm_key_normalization():
    tag = 'A1k llm 输出 key 规范化（键与引用同一函数）'
    raw = {'candidates': [
        {'key': ' Battery ', 'type': 'object', 'name': '电池', 'definition': 'd',
         'fields': {}, 'ownerKey': '', 'evidence': {'_record': ['f-1']},
         'evidenceStatus': 'supported', 'conflicts': []},
        {'key': 'P 1', 'type': 'property', 'name': '额定功率', 'definition': 'd',
         'fields': {'dataType': 'number', 'sourceRef': ' Battery '}, 'ownerKey': 'Battery',
         'evidence': {'definition': ['f-1']}, 'evidenceStatus': 'supported', 'conflicts': []},
    ]}
    cleaned, _dropped = llm._sanitize_candidates(raw['candidates'], {'f-1'})
    check(cleaned[0]['key'] == 'battery' and cleaned[1]['key'] == 'p_1',
          '%s：key 去空白/内部空白折叠为下划线/统一小写/截断 60' % tag,
          actual=[cleaned[0]['key'], cleaned[1]['key']], expected=['battery', 'p_1'])
    check(cleaned[1]['ownerKey'] == 'battery',
          '%s：ownerKey 与 key 同一规范化（大小写不一致被修复，批内引用可解析）' % tag,
          actual=cleaned[1]['ownerKey'], expected='battery')
    check(cleaned[1]['fields'].get('sourceRef') == 'battery',
          '%s：链接端点引用与 key 同一规范化（引用不断链）' % tag,
          actual=cleaned[1]['fields'].get('sourceRef'), expected='battery')
    check('battery_rated_power' in llm.SYSTEM_EXTRACT and 'p' in llm.SYSTEM_EXTRACT,
          '%s：SYSTEM_EXTRACT 已要求对象限定的见名知义 key（软措施）' % tag)


# --- B. 管线集成层（隔离根 + mock 模型） ---------------------------------------------

def new_isolated_root(tag):
    """新临时根 + 该根下的 SQLite；重置引擎后惰性初始化（绝不碰真实 ontology/）。"""
    root = Path(tempfile.mkdtemp(prefix='wiz_key_namespace_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.ensure_ready()
    if str(root) not in sto.resolve_url():
        raise AssertionError('隔离根未生效：%s' % sto.resolve_url())
    return root


def seed_two_fact_task():
    """建任务 + 批次 + 范围 + 2 条事实（各成一批，LLM_BATCH_FACTS=1）。"""

    def body(conn):
        task_id = store.create_task(conn, UID, '跨批键命名空间回归')
        batch_id = store.create_batch(conn, task_id, UID, '', {})
        store.put_scope(conn, task_id, UID,
                        {'goal': '设备管理', 'include': '设备', 'exclude': '', 'relations': '',
                         'coverage': '', 'openQuestions': []}, confirmed=True)
        facts = [{'id': 'f-%d' % n, 'module': 'device', 'locator': {'kind': 'ddl'},
                  'snippet': '设备%d台账 device_table_%d 字段与单位说明' % (n, n),
                  'kind': 'table', 'data': {'table': 'device'}, 'quality': 'high'}
                 for n in (1, 2)]
        store.replace_material_facts(conn, task_id, UID, 'm', facts)
        run_id, _lease = store.create_run(conn, task_id, UID, 'generate', {}, batch_id)
        return task_id, batch_id, run_id

    with sto.write_tx() as tx:
        return tx.run(body)


def b1_pipeline_end_to_end():
    tag = 'B1 管线端到端：两批同名 key 入库后命名空间化且宿主正确'
    original_batch_facts = protocol.LLM_BATCH_FACTS
    protocol.LLM_BATCH_FACTS = 1   # 2 条事实 → 2 批，模型两批都偷懒用 obj/p
    original_extract = llm.extract_candidates
    try:
        def model(_provider, _scope, batch, timeout=None):
            fact_id = str(batch[0]['id'])
            if fact_id == 'f-1':
                candidates = [obj('obj', '电池', 'f-1'), prop('p', '额定功率', 'obj', 'f-1')]
            else:
                candidates = [obj('obj', '逆变器', 'f-2'), prop('p', '额定功率', 'obj', 'f-2')]
            return {'ok': True, 'usage': dict(USAGE), 'rejectedRefs': 0,
                    'candidates': candidates}

        llm.extract_candidates = model
        task_id, batch_id, run_id = seed_two_fact_task()

        def job(user, run):
            pipeline.run_generate(user, task_id, run, batch_id, PROVIDER, resume_mode='auto')

        thread = runner.submit(UID, run_id, job)
        thread.join(JOIN_SECONDS)
        check(not thread.is_alive(), '%s：生成线程已退出' % tag)

        def read_all():
            with sto.read_connection() as conn:
                return store.all_candidates(conn, task_id, UID, batch_id=batch_id)

        rows = read_all()
        keys = sorted(str(row.get('key')) for row in rows)
        check(keys == ['b2:obj', 'b2:p', 'obj', 'p'],
              '%s：批 1 无冲突保留原始键，批 2 冲突键命名空间化（b2:obj/b2:p）' % tag,
              actual=keys, expected=['b2:obj', 'b2:p', 'obj', 'p'])
        props = [row for row in rows if row.get('type') == 'property']
        hosts = sorted(str(row.get('alignedKey')) for row in props)
        check(len(props) == 2
              and hosts == ['property:额定功率#number@电池', 'property:额定功率#number@逆变器'],
              '%s：两条「额定功率」宿主分别为电池/逆变器（对齐键不再错记）' % tag,
              actual=hosts,
              expected=['property:额定功率#number@电池', 'property:额定功率#number@逆变器'])
        by_host = {str(row.get('alignedKey')).split('@')[1]: row for row in props}
        check(by_host.get('电池', {}).get('ownerKey') == 'obj'
              and by_host.get('逆变器', {}).get('ownerKey') == 'b2:obj',
              '%s：ownerKey 指向各自批次的宿主键（交付侧可解析，不悬空）' % tag,
              actual={k: v.get('ownerKey') for k, v in by_host.items()},
              expected={'电池': 'obj', '逆变器': 'b2:obj'})
        check(all((row.get('origin') or {}).get('key') == row.get('key') for row in rows),
              '%s：origin.key 与命名空间化后的候选键一致（合并别名/续跑种子同键空间）' % tag)
    finally:
        llm.extract_candidates = original_extract
        protocol.LLM_BATCH_FACTS = original_batch_facts
        for item in threading.enumerate():
            if item.name.startswith('build-run-'):
                item.join(JOIN_SECONDS)


# --- 入口 ----------------------------------------------------------------------------

PURE_CASES = [
    ('A1 前缀与ownerKey重写', a1_prefix_and_owner_rewrite),
    ('A1e 两批同key属性不同宿主', a1e_two_batches_same_key_different_hosts),
    ('A1f 两批同key对象同名合并', a1f_same_name_objects_merge),
    ('A1g 链接端点跨批重写', a1g_link_refs_rewritten),
    ('A1h 拆批子批同前缀', a1h_split_halves_share_prefix),
    ('A1i 幂等与空键', a1i_idempotent_and_empty),
    ('A1l 冲突条件模式', a1l_conditional_namespace),
    ('A1j 旧格式候选兼容', a1j_legacy_candidates_compatible),
    ('A1k llm key规范化', a1k_llm_key_normalization),
]

ROOTED_CASES = [
    ('B1 管线端到端', b1_pipeline_end_to_end),
]


def main():
    for tag, fn in PURE_CASES:
        print('\n----- %s -----' % tag)
        fn()
    for tag, fn in ROOTED_CASES:
        print('\n----- %s -----' % tag)
        new_isolated_root(tag)
        fn()
    return 0


def shutdown():
    """收尾：确认没有遗留 worker 线程，再销毁全部临时根（绝不碰真实 ontology/）。"""
    leftover = [t for t in threading.enumerate() if t.name.startswith('build-run-')]
    for thread in leftover:
        thread.join(10)
    alive = [t.name for t in leftover if t.is_alive()]
    if alive:
        FAILED.append('worker 线程未退出: %s' % alive)
    if runner._workers:
        FAILED.append('worker 登记残留: %s' % sorted(runner._workers))
    sto.reset_engine()
    for root in ROOTS:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Exception as exc:
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    finally:
        shutdown()
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if code == 0 and not FAILED else 1)
