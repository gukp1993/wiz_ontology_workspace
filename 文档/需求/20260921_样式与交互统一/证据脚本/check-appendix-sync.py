#!/usr/bin/env python3
# 核对开发计划里抄录的附表 B 与脚本产物 out/appendixB.md 是否逐字一致。
# 抄录是一次性动作，但此后任何一方改动都会让两者漂移；这条检查让漂移可被机器发现，
# 而不是靠"由脚本生成，非手抄"这句话自证。
# 用法（在 worktree 根执行）：python3 "文档/需求/20260921_样式与交互统一/证据脚本/check-appendix-sync.py"
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN = os.path.join(HERE, '..', '开发计划.md')
START = '**令牌级 `:root` 差异'
END = '\n### 9.5 '

plan = io.open(PLAN, encoding='utf-8').read()
gen = io.open(os.path.join(HERE, 'out', 'appendixB.md'), encoding='utf-8').read().rstrip()
i = plan.index(START)
j = plan.index(END)
emb = plan[i:j].rstrip()
if emb == gen.rstrip():
    print('OK  附表 B 与 out/appendixB.md 逐字一致（%d 字符）' % len(emb))
    sys.exit(0)
print('DRIFT 嵌入正文 %d 字符 vs 生成产物 %d 字符' % (len(emb), len(gen)))
el, gl = emb.split('\n'), gen.split('\n')
for n in range(max(len(el), len(gl))):
    x = el[n] if n < len(el) else '(缺行)'
    y = gl[n] if n < len(gl) else '(缺行)'
    if x != y:
        print('  首处差异 @ 正文第 %d 行' % (n + 1))
        print('   计划: %s' % x[:180])
        print('   产物: %s' % y[:180])
        break
sys.exit(1)
