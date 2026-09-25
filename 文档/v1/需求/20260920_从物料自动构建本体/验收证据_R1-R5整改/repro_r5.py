"""复现 R5：非连续 pending 时前缀游标漏刷 + 调用方二次处理。
用真实调度器 _run_batches_adaptive，只替换模型调用为立即返回的替身。"""
import sys, threading
sys.path.insert(0, '.')
from workbench.ontology_build import pipeline

CALLED = []
def fake_extract(provider, scope, batch, depth=0):
    CALLED.append(batch['id'])
    return {'ok': True, 'candidates': [{'key': batch['id'], 'type': 'object', 'name': batch['id'],
            'definition': 'x', 'evidence': {'definition': ['f-1']}, 'evidenceStatus': 'supported'}],
            'usage': {}, 'rejectedRefs': 0}

pipeline._extract_batch_with_split = fake_extract

batches = [{'id': 'b1'}, {'id': 'b2'}, {'id': 'b3'}]
# 场景：批 2 已完成（上次运行），本次 pending = [1, 3]
pending = [(1, batches[0]), (3, batches[2])]
done = set()
flushed = []
accumulated = []

def on_result(position, result):
    flushed.append(position)

res = pipeline._run_batches_adaptive(
    'owner', 'run', 'label', batches, pending, {'endpoint': 'x', 'model': 'm'}, {},
    done, accumulated, [{'id': 'f-1'}], on_result=on_result)

print('模型实际被调用:', CALLED)
print('回调落库(flushed):', flushed)
print('返回的 results 键:', sorted(res.keys()))
print('done_positions:', sorted(done))
print()
print('=== 调用方会怎么处理（模拟 pipeline.py:1296-1310 的循环）===')
failed_batches, batch_log = [], []
for position, _batch in pending:
    result = res.get(position) or {'ok': False, 'candidates': [],
                                   'error': '批次未返回结果（可「重试失败批次」）'}
    if not result.get('ok'):
        failed_batches.append(position)
    else:
        done.add(position)
print('  最终 done:', sorted(done))
print('  最终 failed:', failed_batches)
print()
expect_flush = {1, 3}
print('结论:')
print('  应落库批次 %s / 实际落库 %s → %s' % (sorted(expect_flush), sorted(flushed),
      '✅ 正确' if set(flushed) == expect_flush else '❌ 有批次漏刷'))
contradict = set(done) & set(failed_batches)
print('  同一批号同时进 done 和 failed: %s → %s' % (sorted(contradict) or '无',
      '❌ 状态自相矛盾（会误报失败）' if contradict else '✅ 无矛盾'))
