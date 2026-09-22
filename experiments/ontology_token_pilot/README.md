# ontology_token_pilot 试验 CLI（D14）

本体生成控输出 v3 的 A/B/C/D 四臂 token 试点入口（整合计划 v3 §8 D14、§11、§12）。
调度唯一核心是 `workbench/ontology_build/batch_execution.py`（D09 共享执行器），
本包不含任何第二套调度器/状态机/事务代码；持久化用 D12 `ExperimentState`，
评价与脱敏用 D13 `evaluate`，用量口径用 D00 `batch_contracts`。

## 用法（冻结）

```bash
python3 -m experiments.ontology_token_pilot --config <配置路径> --output-dir <独立目录> --mode fake
python3 -m experiments.ontology_token_pilot --config <同一配置> --output-dir <独立目录> --mode real --arms A,D --repeats 1
```

* `--mode fake` 默认：零真实调用、零 workbench 存储/LLM 客户端依赖，可随时跑。
* `--arms` 默认 `A,B,C,D`；`--repeats` 默认 1；`--samples` 默认取配置 `sampleIds`
  全部（可选 `simple,amplified,schema_conflict`）。
* 退出码：`0` 全部 run 成功；`2` 部分受阻（campaign 预算达线 / 模型不可用 / run
  失败或异常，报告 notes 注明）；`1` 参数错误（缺 `--config`、配置非法、臂/样本名非法）。
* 输出：`<output-dir>/report.json` + 可读 `report.md`（含 G2 三项判定：质量门 /
  compact 收益门 / 成本取舍；未跑的项如实标注 `not_run`/`unprovable`）。

回归测试：`python3 tests/run.py --test tests/test_ontology_token_pilot_cli.py`
（全部 fake、tempfile 隔离；含无第二套调度器的 AST 断言）。

## 四臂定义（v3 §11 冻结）

| 臂 | 输出协议 | 调度 | 用途 |
|---|---|---|---|
| A | legacy-v1 | 固定批对照 planner（每 job 固定 `protocol.LLM_BATCH_FACTS`=20 事实） | 对照 |
| B | compact-v1 | 同 A 的固定批 planner | 单独观察协议 |
| C | legacy-v1 | v3 共享核心（`plan_initial` 初始装箱 + 缺省 `pack_next`） | 单独观察调度 |
| D | compact-v1 | v3 共享核心 | 组合 |

A/B 与共享核心的**唯一**差异是注入 planner（`batch_execution._plan_more` 的既有
注入点）；全部差异点登记在每个结果的 `meta.adapterDiffs`，不重写任何"声称等价"的
旧算法。并发全臂 1（串行执行，避免并发成为混杂变量）。

## fake 模型与脚本事件

`adapter.FakeModel(script=[...])` 默认对每次物理请求生成能通过对应 codec decode 的
正常 stop 响应（legacy 用真实 factId 作证据；compact 用局部别名作证据并**逐单元回
coverage**——漏任一单元整叶即 `COVERAGE_INCOMPLETE`，与真实约束一致）。脚本按每次
run 的物理调用序回放（耗尽后回落正常响应）：

| 事件 | 行为 |
|---|---|
| `length` | finish_reason=length 截断（触发共享核心 `split_or_block` 拆分） |
| `rate_limited` | 429（RATE_LIMITED，可重试，验证退避与记账） |
| `bad_json` | 非法 JSON（触发每 job 至多 1 次格式修复） |
| `crash` | 模型崩溃（包装层转 HTTP_UNKNOWN 不可重试，不上抛执行器） |
| `unknown_usage` | 正常 stop 但 usage 缺失（unknown 不计 0 口径） |

脚本经配置 `fakeScript` 数组注入（如 `"fakeScript": ["length"]`）。注意：ok=True 的
响应（含截断内容）会进 campaign 暖缓存——带脚本的 run 之后，同 (臂,样本) 的后续
repeat 会从缓存原样回放这些响应。FakeModel 候选只是**结构合法的冒烟数据**，不代表
真实模型质量；G2 质量判定以 D18 真实试验为准。

## campaign 预算语义

* 预算库：`<output-dir>/states/<campaign>/campaign.sqlite3`（D12 独立 SQLite，
  `campaign:budget:v1` 键存累计：物理请求数 / 已知 completion 累计 / 活跃毫秒）。
  跨臂、样本、重复与续跑共享，**重跑同 output-dir 不重置**；模型响应暖缓存
  （KV 键 = codec+messages+max_tokens 摘要）也放同一库，跨 repeat 共用——第二次
  repeat 全部命中缓存、零物理调用。
* 余量执行：每次 run 前 `余量 = campaign 上限 − 累计`，作为 run 级 budget
  （`maxAttempts` / `maxWallMs` / `completionSoftLimit`）直接传共享执行器——达线
  不再派发（`ATTEMPT_BUDGET_EXCEEDED` / `COMPLETION_SOFT_LIMIT` /
  `WALL_TIME_BUDGET_EXCEEDED`），在途可能使最终值略超线（契约口径）。
* 记账口径：物理请求计数**逐次即时落库**（先计数后返回，崩溃窗口最多多计一次，
  宁可保守）；已知 completion / 活跃耗时按本次 runAttempt 已收口尝试补记（跨
  resume 不双计；崩溃在途尝试 usage 未知，本就不进已知口径）。
* 缺省上限（v3 §11 campaign 档）：128 物理请求 / 200000 已知 completion /
  7200000ms 活跃。注意默认预算**跑不完全矩阵**（三样本 × 四臂约 114 次物理调用，
  其中 C/D 的软目标装箱会拆出较多小批）——扩额须在配置显式给出，本 CLI 不自行加预算。
* 已知限制：预算库随 output-dir 走，换 output-dir 等于新 campaign 库（v3 要求
  "换 output-dir 不得重置预算"，当前实现未做跨目录共享，见"限制"节）。

## 输出目录结构

```
<output-dir>/
├── report.json                    # D13 build_report + g2 三项判定（已脱敏）
├── report.md                      # 可读报告
├── results/<campaign>/
│   └── <sample>-<arm>-r<n>.json   # 单次 run 结果（usage/质量/事件统计/meta，已脱敏）
└── states/<campaign>/
    ├── campaign.sqlite3           # campaign 预算累计 + 模型响应暖缓存
    └── <sample>-<arm>-r<n>.sqlite3  # 每 (sample, arm, repeat) 独立计划/候选库
```

单次结果 `meta` 关键字段：`codec` / `planner` / `adapterDiffs` / `schemaVersion`（=2）
/ `physicalCalls` / `cacheHits` / `jobCount` / `runAttempt` /
`campaignBudgetRemainingAtStart`。事件统计 `events`：`jobSplit`（拆分次数）、
`formatRepair`、`networkRetry`、`jobFailed`、`plannedJobs` 等。

续跑语义：结果文件已存在且终态（succeeded/failed/blocked）的 run 跳过不重做
（报告 notes 注明"续跑跳过"，历史受阻结论保留）；崩溃中断的 run（有状态库、无
终态结果）自动恢复——在途尝试记 `interrupted_unknown`、failed 叶重排队、只补未
成功部分。要重跑某个已终态 run，须显式删除该 run 的结果 JSON 与状态 sqlite3。

## 配置（非敏感；不含 key）

```json
{
  "providerId": "prov-id",              // 必填；real 模式经 workbench.llm_providers 解析
  "campaignId": "pilot-2026xxxx",       // 必填；隔离 states/results 子目录
  "goldenDir": "…/golden",              // 必填（相对路径按配置文件所在目录解析）
  "sampleIds": ["simple", "amplified", "schema_conflict"],
  "profile": {"contextTokens": 131072, "outputLimitTokens": 32000,
              "requestOutputTokens": 32000, "targetRatio": 0.5},
  "scope": {"goal": "…"},
  "campaignBudget": {"maxAttempts": 128, "maxCompletionTokens": 200000, "maxWallMs": 7200000},
  "samplesDir": "…/samples",            // 可选，默认仓库 tests/fixtures/ontology_token_pilot/samples
  "fakeScript": ["length"]              // 可选，仅 fake 模式
}
```

样本事实由 `adapter.load_sample_facts` 确定性合成：JSON/JSON-LD 按 dict 节点
（`@graph` 节点带 nodeId 定位器）、SQL 按 CREATE TABLE（ddl 定位器）+ 其余文本、
其他按纯文本；每样本事实集经 `semantic_units.build_targets` 进入计划。

## 边界：默认 fake，真实调用需授权

* **默认 fake**；`--mode real` 是显式授权动作：仅在 real 分支 import workbench
  存储层/`llm_providers`（fake 模式零依赖），providerId 解析不到/缺 endpoint/model
  → 全部 run 记"未派发"并以退出码 2 报告，不发任何真实请求。
* provider dict（含 api_key）只存在于内存，**绝不进 CLI 参数、配置、报告、日志、
  结果文件**；结果落盘前统一过 D13 `redact_report`（密钥样式串掩码、prompt/response
  键删除）。
* 真实试验（收益结论、codec 上线建议）归 **D18**：本 CLI 只交付能力，real 模式
  尚未实测（未接真实 provider 跑过），不据此宣称任何真实 token 收益。
