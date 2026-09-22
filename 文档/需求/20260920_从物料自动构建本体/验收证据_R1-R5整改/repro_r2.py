"""R2 复现：跨批发现冲突后，候选是否仍保持默认 include（未经人工确认）。"""
import sys
sys.path.insert(0, '.')
from workbench.ontology_build import pipeline, alignment

fact_ids = ['bf-aaa', 'bf-bbb']

# 两批各自给了同名对象「电池」，定义冲突，但各自都有合法证据
b1 = [{'key': 'obj', 'type': 'object', 'name': '电池', 'definition': '储能电池设备',
       'fields': {}, 'evidence': {'definition': ['bf-aaa']},
       'evidenceStatus': 'supported', 'conflicts': []}]
b2 = [{'key': 'obj', 'type': 'object', 'name': '电池', 'definition': '电池管理系统',
       'fields': {}, 'evidence': {'definition': ['bf-bbb']},
       'evidenceStatus': 'supported', 'conflicts': []}]

# 步骤 1：逐批 verify（模拟 pipeline 逐批校验）
v1, _ = pipeline.verify_candidates(b1, fact_ids)
v2, _ = pipeline.verify_candidates(b2, fact_ids)
print('逐批 verify 后：')
for name, v in (('批1', v1[0]), ('批2', v2[0])):
    print('  %s: evidenceStatus=%s decision=%s' % (name, v.get('evidenceStatus'), v.get('decision')))

# 步骤 2：跨批 align（合并同名同类型）
al = alignment.align(v1 + v2)
merged = al['candidates'][0]
print()
print('跨批 align 后：')
print('  name=%s' % merged.get('name'))
print('  evidenceStatus=%s  ← 已降级为冲突' % merged.get('evidenceStatus'))
print('  decision=%s        ← 决定是否被重算？' % merged.get('decision'))
print('  conflicts=%d 条' % len(merged.get('conflicts') or []))

# 步骤 3：合并后二次 verify（pipeline.py:1373 真实调用点）
final, report = pipeline.verify_candidates(al['candidates'], fact_ids)
c = final[0]
print()
print('二次 verify 后（进入 adapt/交付前的最终态）：')
print('  evidenceStatus=%s' % c.get('evidenceStatus'))
print('  decision      =%s' % c.get('decision'))
print('  reviewed      =%s' % c.get('reviewed'))
print('  reason        =%r' % c.get('reason'))
print('  conflicts     =%d 条' % len(c.get('conflicts') or []))
print()
if c.get('evidenceStatus') == 'conflict' and c.get('decision') == 'include' and not c.get('reviewed'):
    print('  ❌ 复现成功：冲突项保持 include，且 reviewed/reason 均为空')
    print('     → 交付端 _selected_candidates 只按 decision==include 选取')
    print('     → 未经人工确认的冲突项会进入默认交付集合')
else:
    print('  ✅ 未复现')
