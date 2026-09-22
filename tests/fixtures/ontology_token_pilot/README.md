# ontology_token_pilot 合成材料与人工金样（D11）

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§11 样本要求、§8 D11 行；
候选协议对齐 `workbench/ontology_build/batch_contracts.py`（normalized candidate：
key/type/name/definition/fields/ownerKey/evidence/evidenceStatus/conflicts/rejectedRefs）；
定位器形态对齐 `文档/接口文档/08-从物料自动构建本体接口.md` §1.3。

**合成声明：本目录全部内容为确定性合成示例数据，不含任何真实业务数据、真实域名、
密钥或个人信息。** 任何内容修改必须同步更新 `manifest.json` 的 sha256 并重跑
`python3 tests/run.py --test tests/test_ontology_token_pilot_fixtures.py`。

## 三类样本与判定要点

### 样本① `samples/simple/`（简单样本，对标原 8KB 规模）

- `station_topology.jsonld`（约 8KB）：JSON-LD `@graph`，储能领域通用示例
  （电池储能单元/双向变流器/能量管理系统/并网计量点 + 3 条链接关系），字段名自造。
  判定要点：对象/属性/链接三类候选齐全；单位（kW、kWh、% 等）只随证据说明、不得臆造；
  属性 ownerKey 归属正确。
  **注意：这是合成对标样本，不是原 8KB 真实文件**；原文件若由 D18 提供须另行登记 hash。
- `gateway_config.json`（约 4KB）：嵌套对象 + 数组的普通 JSON 配置。
  判定要点：array/struct/boolean 等 dataType 标注正确，嵌套层级不丢失。

### 样本② `samples/amplified/device_instances.json`（重复实例逐级放大）

同一模式 60 个设备实例，逐级放大且**必须保留**：
- 稀有字段：`deratingCurveRef`（仅 SYN-INV-007）、`parallelBusId`（仅 SYN-INV-033）、
  `transformerTapPosition`（仅 SYN-INV-052）；
- 单位差异：多数实例 `ratedPowerKw`，个别实例（15/31/47）用 `ratedPowerW`；
- 异常枚举值：`status:"UNKNOWN_3"`（SYN-INV-024）、`status:"MAINTENANCE_HOLD"`（SYN-INV-049）；
- 晚出现结构：`leakDetectionOhm`/`harmonicThresholdPct`/`antiIslandingMode` 仅后半
  （第 41 台起）实例携带；第二级放大字段 `coolingMode`/`cabinetIpRating` 自第 21 台起出现。

判定要点：不同实例、单位、枚举、晚出现稀有字段不得按同名折叠；序列化规模 ≥ 40KB。
结构断言依据金样 `structure` 块程序化判定。

### 样本③ `samples/schema_conflict/`（混合类型 + 冲突）

- `inventory_a.json` / `metering_b.json`：两个同名不同主体的 `config` 对象
  （库存域 vs 计量域）——金样 `mustNotMerge` 判定为两个对象、不合并；
  `inventory.batch.meterPointRef` 跨文件引用 `metering_b.json` 的
  `urn:ess-sample:meter-point-m1`（期望产生跨文件链接候选）；
  两文件对同一结算电价属性给出不同 dataType（number vs text）——
  金样 `expectedConflicts` 要求保留冲突、不得静默取任一侧。
- `dispatch_logic.sql`：文本片段（DDL + rule/action 注释块），**不是 JSON**，
  由材料解析器按 ddl/text 类事实处理（定位器 `{kind:'ddl',file,line,table,column}`）；
  覆盖一条 rule（R-SYN-101）与一条 action（A-SYN-07）。

## 金样格式（schemaVersion 1）

- `goldenFor`：样本文件路径（相对本目录，可为多个文件）。
- `expectedCandidates[]`：每条含 `key`/`type`/`name`；property 必带 `ownerKey`；
  `fields` 为**子集断言**（候选 fields 须包含此处每个键且值相等；未列字段不作要求；
  冲突属性 fields 置空对象，改由 expectedConflicts 判定）。
- `key` 是**金样语义身份键空间**（如 `battery_ess.rated_power`），不要求与模型输出的
  批内临时 key 字面一致；D13 评测负责把候选对齐到语义身份再比对。
- `evidence`：按语义身份描述证据（`file` + `identity` + `locatorKind`/`locatorHint`），
  **不绑定具体 factId**（factId 由解析决定）；locatorHint 描述语义位置，不逐字断言
  解析器 path 语法。
- `mustNotMerge[]`：同名异主体分组，组内 key 互异且都出现在 expectedCandidates。
- `expectedConflicts[]`：每条含 `field` 与两侧 `sides`（source/locator/expect/note）。
- `crossFileRelations[]`：跨文件关系（fromKey/toFile/targetIdentity）。
- `minimumCounts`：按候选类型的数量下限。
- `structure`（仅样本②）：instanceCount/lateFields/tier2Fields/rareFields/unitVariants/
  abnormalEnumValues/minSerializedBytes，供 fixture 测试程序化断言。

## 规模声明与泄漏政策

- 规模为**合成对标**：样本①对标“原 8KB JSON/JSON-LD”量级（JSON-LD 6–12KB、
  配置 3–6KB），样本② ≥ 40KB；不是 D18 原始真实文件，原文件提供后另行登记。
- 全部文件不得包含真实域名、密钥样式串（`sk-`、`api_key` 等）或个人信息；
  fixture 测试对 samples/ 与 golden/ 全量扫描断言。
