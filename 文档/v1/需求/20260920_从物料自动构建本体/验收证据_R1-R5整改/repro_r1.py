"""R1 复现：两批各自合法的同名 key → 全局对齐后属性宿主错误。"""
import sys, json
sys.path.insert(0, '.')
from workbench.ontology_build import alignment

# 批 1：对象「电池」+ 它的属性「额定功率」，模型给的批内 key 都是简单名
b1 = [
  {'key': 'obj', 'type': 'object', 'name': '电池', 'definition': '储能电池',
   'evidence': {'definition': ['f1']}, 'evidenceStatus': 'supported'},
  {'key': 'p', 'type': 'property', 'name': '额定功率', 'definition': '电池额定功率',
   'ownerKey': 'obj', 'fields': {'dataType': 'number'},
   'evidence': {'definition': ['f1']}, 'evidenceStatus': 'supported'},
]
# 批 2：对象「逆变器」+ 它的属性「额定功率」——模型同样用 obj/p（它只知道批内唯一）
b2 = [
  {'key': 'obj', 'type': 'object', 'name': '逆变器', 'definition': '储能逆变器',
   'evidence': {'definition': ['f2']}, 'evidenceStatus': 'supported'},
  {'key': 'p', 'type': 'property', 'name': '额定功率', 'definition': '逆变器额定功率',
   'ownerKey': 'obj', 'fields': {'dataType': 'number'},
   'evidence': {'definition': ['f2']}, 'evidenceStatus': 'supported'},
]

out = alignment.align(b1 + b2)
print('=== 全局对齐结果 ===')
props = [c for c in out['candidates'] if c.get('type') == 'property']
objs  = [c for c in out['candidates'] if c.get('type') == 'object']
print('对象:', [c['name'] for c in objs])
print('属性数:', len(props), '（期望 2：电池的额定功率 + 逆变器的额定功率）')
for p in props:
    print('   属性「%s」alignedKey=%s' % (p.get('name'), p.get('alignedKey')))
print()
expected = 2
if len(props) < expected:
    print('❌ 复现：两个不同对象的属性被误合并为 1 项')
    print('   键 =', props[0].get('alignedKey') if props else None)
    print('   → 逆变器的「额定功率」归属丢失，被并入电池')
else:
    print('✅ 未复现')
