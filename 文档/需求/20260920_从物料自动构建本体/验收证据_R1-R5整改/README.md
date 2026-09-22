# R1–R5 整改验收证据（2026-09-22）

验收对象：`22a60e5 fix(ontology-build): 整改五项正确性缺陷 R1–R5`（基线 `805e1d9`，分支 `codex/auto_build`）。

## 复现脚本（修复前全部复现失败 → 修复后全部「未复现」）

| 脚本 | 对应缺陷 | 用法（在 worktree/auto_build 下） |
|---|---|---|
| `repro_r1.py` | R1 跨批临时键冲突致属性宿主错误 | `WIZ_WORKBENCH_ROOT=/tmp/v_r1 python3 <脚本>` |
| `repro_r2.py` | R2 跨批冲突默认纳入交付 | 同上（换临时根目录） |
| `repro_r3.py` | R3 去重误剔 + 报告失真 | 同上 |
| `repro_r4.py` | R4 拆批半失败误判成功 | 同上 |
| `repro_r5_final.py` | R5 非连续续跑静默丢结果 | 同上（`repro_r5.py` 为初版判定脚本，留档） |

脚本均为纯内存合成数据 + 替身模型调用，`WIZ_WORKBENCH_ROOT` 指向临时目录，不触碰真实数据。

## 修复后终验输出（2026-09-22，代码 22a60e5）

```
repro_r1        ✅ 未复现
repro_r2        ✅ 未复现
repro_r3        ✅ 未复现（异文件同值两条均保留，哈希 a55fb734…≠330ad104…；真副本仍判重）
repro_r4        ✅ 未复现（部分失败父批按失败记账，成功半候选保留，失败事实 id 随 error 上抛）
repro_r5_final  ✅ 未复现（pending 全部批次均已独立落库，无静默丢失）
```

## 回归

- `python3 tests/run.py all` → **60/60 通过**
- `python3 tests/test_ontology_build.py` → **250/250（零既有断言修改）**
- 新增套件：batch_accounting 36 / key_namespace 30 / conflict_gate 29 / fact_identity 21（共 116 项）
- ruff 全部改动文件 All checks passed
