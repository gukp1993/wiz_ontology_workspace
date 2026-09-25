"""复现 R4：拆批后一半成功一半失败，父批是否被标成功（丢失半批事实的候选）。

补丁目标说明：真实拆批逻辑在 pipeline._extract_batch_with_split 内部（递归调用
自身 + llm.extract_candidates）。要让它真的执行拆批合并分支，必须替身
llm.extract_candidates（首调整批截断、第二/三调分别为左/右半批），而不是替身
_extract_batch_with_split 本身（那样真实逻辑根本不会运行）。
"""
import sys
sys.path.insert(0, '.')
from workbench.ontology_build import llm, pipeline

calls = []
def make(left_ok, right_ok):
    def fake(provider, scope, batch, timeout=None):
        calls.append(len(batch))
        ids = {str(f.get('id')) for f in batch}
        # 按批内事实内容定响应（与调用顺序/重试无关）：整批（含 f1 与 f11）截断，
        # 左半（f1..f10）按 left_ok，右半（f11..f20）按 right_ok。
        if 'f1' in ids and 'f11' in ids:
            return {'ok': False, 'candidates': [], 'usage': {},
                    'error': 'LLM 输出被截断（超出 max_tokens），请简化代码或计算规则后重试'}
        ok = left_ok if 'f1' in ids else right_ok
        return {'ok': ok, 'candidates': ([{'key': 'k%s' % sorted(ids)[0], 'type': 'object',
                'name': 'n', 'definition': 'd'}] if ok else []),
                'usage': {}, 'rejectedRefs': 0,
                'error': None if ok else 'LLM 调用失败：无法连接接口（超时或网络不可达）'}
    return fake

batch = [{'id': 'f%d' % i} for i in range(1, 21)]   # 20 条事实
original = llm.extract_candidates
try:
    for title, left_ok, right_ok in (('左半成功、右半失败', True, False),
                                     ('右半成功、左半失败', False, True)):
        print('=== 场景：%s ===' % title)
        calls.clear()
        llm.extract_candidates = make(left_ok, right_ok)
        r = pipeline._extract_batch_with_split({'endpoint': 'x', 'model': 'm'}, {}, batch)
        print('  返回 ok =', r.get('ok'))
        print('  候选数 =', len(r.get('candidates') or []), '（成功半的候选应保留，不浪费）')
        print('  错误信息 =', str(r.get('error'))[:120])
        print('  failedFactIds =', (r.get('failedFactIds') or [])[:4],
              '…共 %d 条' % len(r.get('failedFactIds') or []))
        print('  → 父批被判为 %s' % ('✅ 成功（会记入 done）' if r.get('ok')
                                  else '❌ 失败（不进 done，重试整批重跑）'))
        print()
finally:
    llm.extract_candidates = original

r_left = None
calls.clear()
llm.extract_candidates = make(True, False)
try:
    r_left = pipeline._extract_batch_with_split({'endpoint': 'x', 'model': 'm'}, {}, batch)
finally:
    llm.extract_candidates = original

if r_left is not None and not r_left.get('ok') and r_left.get('candidates') \
        and '拆批后部分失败' in str(r_left.get('error')):
    print('✅ 未复现：拆批部分失败时父批按失败记账（不进 done），成功半候选保留，'
          '失败事实 id 随 error 上抛——整批可重试、无静默丢失')
else:
    print('❌ 复现：拆批一半成功一半失败时父批被判成功，失败半批事实的候选永久缺失')
