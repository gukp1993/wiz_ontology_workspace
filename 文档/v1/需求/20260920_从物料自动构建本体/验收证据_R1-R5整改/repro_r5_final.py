"""R5 终局验证：漏落的批次是否会导致"运行成功但候选永久缺失"。"""
import sys, time
sys.path.insert(0, '.')
from workbench.ontology_build import pipeline

def fake_extract(provider, scope, batch, depth=0):
    time.sleep(0.05)
    return {'ok': True, 'candidates': [{'key': 'k', 'type': 'object', 'name': '对象-%s' % batch[0],
            'definition': 'd', 'fields': {}, 'evidence': {}, 'evidenceStatus': 'supported',
            'conflicts': []}], 'usage': {}, 'rejectedRefs': 0}
pipeline._extract_batch_with_split = fake_extract

accumulated, flushed = [], []
failed_batches, done_positions = [], {2}          # 场景：批 2 上轮成功，批 1、3 待重跑
batches = [['f1'], ['f2'], ['f3']]

def flush_batch(position, result):
    flushed.append(position)
    if result.get('ok'):
        done_positions.add(position)
        accumulated.extend(result.get('candidates') or [])
    else:
        failed_batches.append({'position': position, 'error': result.get('error')})

pipeline._run_batches_adaptive('u', 'r', 'label', batches, [(1, batches[0]), (3, batches[2])],
    {'timeout': 60, 'model': 'm', 'endpoint': 'e'}, {}, done_positions, accumulated,
    [{'id': f} for f in ('f1', 'f2', 'f3')], on_result=flush_batch)

print('=== R5 终局 ===')
print('  实际落库批次    :', flushed, '（期望 [1, 3]）')
print('  done_positions  :', sorted(done_positions))
print('  failed_batches  :', failed_batches)
print()
# 核心不变量：pending 里的每个批次要么已落库、要么已计入失败——没有第三种结局。
pending_positions = [1, 3]
failed_set = {item['position'] for item in failed_batches}
missing = [p for p in pending_positions if p not in flushed and p not in failed_set]
print('  调用方判定：')
print('    未落库也未计失败的批次: %s' % (missing or '无'))
print()
if missing:
    print('  ❌ 终局确认：批 %s 的结果既未落库也未计失败' % missing)
    print('     → 运行会报成功，但该批候选永久缺失、不报错、重试也不补（静默数据丢失）')
else:
    print('  ✅ 未复现：pending 全部批次（1、3）均已独立落库，无静默丢失')
