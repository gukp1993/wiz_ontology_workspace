"""R3 复现：不同主体/字段但相同取值的事实，是否被全池去重误当重复。"""
import sys
sys.path.insert(0, '.')
from workbench.ontology_build import retrieval, alignment

# 两条事实：不同文件、不同字段路径、不同主体，但取值都是 220
facts = [
    {'id': 'bf-1', 'kind': 'jsonLeaf', 'module': 'json',
     'locator': {'file': 'a.json', 'path': '$.battery.voltage'},
     'snippet': '220', 'data': {'field': 'battery.voltage', 'value': '220'}, 'quality': 'high'},
    {'id': 'bf-2', 'kind': 'jsonLeaf', 'module': 'json',
     'locator': {'file': 'b.json', 'path': '$.motor.power'},
     'snippet': '220', 'data': {'field': 'motor.power', 'value': '220'}, 'quality': 'high'},
]

# 复刻 pipeline.py:1191-1197 的全池去重
seen, kept = set(), []
for f in facts:
    d = retrieval.snippet_digest(f)
    if d in seen:
        continue
    seen.add(d)
    kept.append(f)

print('=== R3 复现结果 ===')
print('  输入事实        : 2 条（battery.voltage=220 / motor.power=220，不同文件不同字段）')
print('  去重后保留      : %d 条 → %s' % (len(kept), [f['id'] for f in kept]))
print('  snippet 哈希    :', [retrieval.snippet_digest(f)[:16] for f in facts])
print('  两条哈希是否相同:', retrieval.snippet_digest(facts[0]) == retrieval.snippet_digest(facts[1]))
print()
g = alignment.group_evidence(facts, ['bf-1', 'bf-2'])
print('  分组统计 duplicates:', g['stats'].get('duplicates'), '（对外报告的口径）')
print()
if len(kept) == 1:
    print('  ❌ 复现成功：不同主体/字段的同值事实被剔除了 1 条')
    print('     → 该事实不再进入模型，对应业务线索永远抽不出来')
    print('     → 而分组统计 duplicates=0，与真实删除量不一致（对外报告失真）')
else:
    print('  ✅ 未复现')

# 对照：真正的文件副本（同文件同字段）本应判重
dup = [dict(facts[0]), dict(facts[1], locator=facts[0]['locator'], data=facts[0]['data'])]
seen2, kept2 = set(), []
for f in dup:
    d = retrieval.snippet_digest(f)
    if d in seen2: continue
    seen2.add(d); kept2.append(f)
print()
print('  对照（真副本，同文件同字段）: 去重后保留 %d 条 ← 这种情况判重是对的' % len(kept2))
