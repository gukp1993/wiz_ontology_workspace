# semantica 自动化图谱构建源码调研（2026-09-21）

> **性质**：源码级调研材料，供白名单第三项"semantica 技术画像"（W11 源码面 + 储能fdev 数据面）引用。本文不是正式画像交付，不占用其冻结的四段产出结构。
> **调研方法**：浅克隆上游仓库至 `/tmp/semantica-research`（2026-09-21，v0.6.8 线，13.3k★ / MIT / 2994 commits），三路并行代码核查（文件解析 / 准确性机制 / 流水线与 LLM 接线）+ 核心文件精读。全部结论核到源码 `文件:行号`。
> **调研对象**：[semantica-agi/semantica](https://github.com/semantica-agi/semantica)——定位为开源 Palantir Ontology 替代品的 Python 库（图原生上下文基础设施）。

## 摘要

1. **默认链路完全不依赖 LLM**：NER 用 spaCy、关系/三元组用正则与依存句法；LLM 是逐环节显式 opt-in（`method="llm"` 或传入 provider），失败即回退规则结果，不阻断下游。
2. **本体生成是双轨设计**：默认从图数据做确定性推断（频率门 + 命名归一 + 层次启发式）；从文本 LLM 直出 ontology JSON 是独立辅助路径，单 prompt、无接地、无迭代，实现明显朴素。
3. **准确性保障以确定性机制为主**：SHACL、约 20 项质量门、词表接地校验、冲突七种仲裁（可升级人工）、PROV-O 哈希链溯源、bootstrap 强制 draft 人工审批——质检层自述 "needs no LLM and is fully unit-testable"。
4. **文档与实现有三处不符**：宣称 HermiT/Pellet 推理实为占位 `pass`；event_detector 的 `method="llm"` 是从未接线的假选项；本体复用判定仅命名空间前缀两档。技术画像引用其宣传能力时须以源码为准。

---

## 1. 整体流程

### 1.1 端到端形态

官方 README "Ontology-to-Knowledge-Graph in One Pass" 示例：

```
FileIngestor.ingest_directory()                          # 摄取（39 种格式白名单）
→ NamedEntityRecognizer.process_batch(texts)             # 实体识别（默认 spaCy）
→ GraphBuilder(merge_entities=True).build(sources)       # 建图（可内部再抽取）
→ OntologyGenerator.generate_ontology({entities, relationships})  # 六阶段生成本体
→ OntologyValidator().validate(ont)                      # 校验
→ RDFExporter().export(..., format="turtle")             # 导出 OWL/Turtle
```

cookbook 入门示例的骨架相同：ingest → parse → 抽取（先 NER，再把 mentions 传给关系抽取）→ GraphBuilder.build → 可视化/导出。

### 1.2 关键内部结构

**GraphBuilder 支持文本直通**（`kg/graph_builder.py:448-505`）：直接传字符串/文本列表时，builder 内部自建 NER / 关系 / 三元组抽取器（带实例缓存，:118），参数默认 `ner_method="ml"`（spaCy，:460）、`relation_method="pattern"`（:461）、`triplet_method="pattern"`（:462）——即默认全规则。LLM 三元组里没匹配到 NER 实体的端点会被提升为合成实体（type="UNKNOWN"，:196-260）。初始化按开关挂 `EntityResolver`（merge_entities，默认 False，strategy="fuzzy"）和 `ConflictDetector`（resolve_conflicts，默认 True，:142-163）。

**本体生成六阶段**（`ontology/ontology_generator.py:124-234`）：

| 阶段 | 内容 | 实现位置 |
|---|---|---|
| ① 语义网络解析 | 实体按 type 分组成 concepts；关系端点经别名索引解析出 source_type/target_type | :251-425 |
| ② 类推断 | 频率门过滤后成类（详见 §4.1） | `_stage2_yaml_to_definition` |
| ③ 属性推断 + OWL 类型映射 | owl:Class / owl:ObjectProperty / owl:DatatypeProperty，补 URI | `_stage3_definition_to_types` |
| ④ 层次构建 | 命名模式找父类 + DFS 环检测 | `_stage4_hierarchy_generation` |
| ⑤ TTL 生成 | rdflib 序列化（由 OWLGenerator 承担，导出 owl/ttl/rdf/json-ld） | — |
| ⑥ 校验 | 调 OntologyValidator，结果写回 `ontology["validation"]` | :208-221 |

### 1.3 编排层（pipeline 模块）

真 DAG 而非纯顺序（`semantica/pipeline/`）：

- `PipelineBuilder.add_step(name, type, **config)` / `connect_steps()` / `set_parallelism()` / `build()`（pipeline_builder.py:120/163/187/208），支持 JSON 序列化与校验。
- 执行引擎：串行拓扑排序（execution_engine.py:697）；并行按依赖分层（DFS 求 level，:464-508，环直接抛 ValidationError）；并行门槛严格——整层步骤必须显式 `parallel_safe=True`、输入为 dict、无 delta_mode（:511-524）。
- 失败重试：`RetryPolicy` 默认 `max_retries=3, backoff_factor=2.0, initial_delay=1.0, max_delay=60.0`，指数/线性/固定三种策略，按 step_type 可设（failure_handler.py:67-77）。
- 内置模板：`document_processing`（ingest→parse→normalize→extract→embed→build_kg）、`rag_pipeline`、KG 模板（ingest_sources→extract_entities→extract_relations→deduplicate→resolve_conflicts→build_graph）、ontology 模板（pipeline_templates.py:86+）。
- 另有 pause/resume/stop/progress（execution_engine.py:755-803）。

---

## 2. 文件解析支持

### 2.1 摄取白名单

`utils/constants.py:49-73`，共 39 种扩展名，单文件上限 100MB（file_ingestor.py:595-603），类型检测三级（扩展名 → MIME → magic number，:120-210）：

- **文档 17 种**：pdf, docx, doc, txt, html, xml, json, jsonl, ndjson, csv, xlsx, pptx, parquet, pq, arrow, feather, ipc
- **图片 8 种**：jpg, jpeg, png, gif, bmp, tiff, webp, svg
- **音频 7 种**：mp3, wav, flac, aac, ogg, wma, m4a
- **视频 7 种**：mp4, avi, mov, wmv, flv, webm, mkv

### 2.2 解析库对照（逐个从 import 确认）

| 格式 | 库 | 来源 | 备注 |
|---|---|---|---|
| PDF | pdfplumber（延迟 import） | pdf_parser.py:120 | |
| DOCX/DOC | python-docx | docx_parser.py:37-41, 137 | doc 后缀也走 docx parser |
| XLSX | pandas + openpyxl（engine 二选一，默认 pandas） | excel_parser.py:35-38, 94 | |
| CSV | 标准库 csv.DictReader | csv_parser.py:30 | |
| JSON | 标准库 json | json_parser.py:30 | |
| HTML | BeautifulSoup（html.parser） | html_parser.py:37, 148 | |
| XML | 标准库 ElementTree | xml_parser.py:32 | |
| PPTX | python-pptx（延迟 import） | pptx_parser.py:99 | |
| TXT | `open()` 直接读 | document_parser.py:362-381 | |
| YAML | PyYAML safe_load | structured_data_parser.py:33 | |
| Email(.eml) | 标准库 email | email_parser.py:35-40 | |
| 代码 | ast（Python）+ 正则（JS/Java 的函数/类/import/注释） | code_parser.py:36-37, 342-374 | 无 tree-sitter |
| 图片 | PIL/Pillow（含 EXIF） | image_parser.py:35 | |
| 图片 OCR | pytesseract + Tesseract | image_parser.py:44, 182-196 | **默认关闭**，需 `extract_text=True`；带 bbox 与置信度；未装 tesseract 报错 |
| 音频 | mutagen | media_parser.py:204 | 只取时长/元数据，**无语音转写** |
| 视频 | ffprobe（subprocess） | media_parser.py:244, 276-284 | 只取元数据，无帧提取 |
| JS 渲染网页 | Selenium webdriver | web_parser.py:312 | |
| 任意文档（可选） | docling（DocumentConverter） | docling_parser.py:54, 156-167 | 可选依赖，import 失败静默置 None；未装时 `parse_document(method="docling")` **静默回退默认 parser 不报错**（methods.py:179-199, 254-261） |

### 2.3 已确认的坑

1. **白名单 ≠ 主解析能力**：`DocumentParser` 主分派只认 pdf/docx/html/text 四类（document_parser.py:185-194）；xlsx/pptx/json/csv/xml 须走 `StructuredDataParser`（structured_data_parser.py:105-111, 257-261）或 `parse_json/parse_csv/parse_xml` 便捷函数。"白名单接受上传"不等于"主入口能解析"。
2. **解析输出无统一 Document 类**：各 parser 返回各自 dict/dataclass（PDF `{"metadata","pages","full_text","total_pages"}`，DOCX 含 paragraphs/tables/sections/comments 等）；ingest 层统一为 `FileObject{path,name,size,file_type,mime_type,content,metadata}`。
3. **ingest/parse 层不分块、不并发**：多文件为顺序 for 循环 + `continue_on_error` 默认 True（file_ingestor.py:513-540）；`parse_batch` docstring 宣称 concurrent 但代码无并发原语。
4. 分块统一在 split 层：`TextSplitter` 默认 `chunk_size=1000 字符、chunk_overlap=200`（split/splitter.py:63-97）；方法注册表含 recursive（默认）/token/sentence/paragraph/character/word/semantic（spaCy/transformer）/sliding_window/structural/table 等（split/methods.py:199-652）。

### 2.4 数据源（文件之外）

ingest/ 下 30+ 连接器：REST、Web、RSS/Atom、Email、代码仓库（含 commit）、GDrive、HuggingFace、MCP、数据库（SQL/Mongo/DuckDB/Elastic）、云数仓（Snowflake/Redshift/BigQuery/Databricks）、SaaS（Salesforce/SAP OData/PowerBI）、S3/GCS/Azure（内置于 FileIngestor，file_ingestor.py:225-395）等。

---

## 3. LLM 参与环节与具体工作

### 3.1 架构决定

**默认全链路零 LLM**（spaCy + 正则/规则），LLM 逐环节显式 opt-in（`method="llm"` 或传入 provider 实例），**所有 LLM 调用失败时回退非 LLM 结果，不阻断下游**（如 llm_extraction.py:280-285 增强失败原样返回）。

### 3.2 环节判定表

| 环节 | 默认方法 | LLM 路径 | LLM 具体做什么 | 证据 |
|---|---|---|---|---|
| NER | spaCy `en_core_web_sm` | `method="llm"` | 抽实体 + 0-1 置信度 JSON；可多方法投票合并（fallback/union/consensus） | ner_extractor.py:123,136,198-215；methods.py:1186,1334 |
| 关系抽取 | pattern 正则 | `method="llm"` | 抽 subject-predicate-object；规则族另有 regex/co-occurrence/similarity/**spaCy 依存句法** | relation_extractor.py:84,97-105；methods.py:1455/1564/1610/1641/1760/1918 |
| 三元组抽取 | pattern | `method="llm"` | typed schema 约束输出 | triplet_extractor.py:86,93-98；methods.py:2444/2491/2532/2595,2743 |
| 事件检测 | 正则 | ⚠️ 构造器签名写 `method="llm"` 但 **从未被使用**，全文件无 LLM 调用代码 | 无（伪选项） | event_detector.py:88,98,331-336 |
| 指代消解 | 纯规则 | 无（docstring 提及 llm 但参数只透传给底层 NER） | 代词正则表 + 类型兼容表（he/she→PERSON；it→ORG/GPE/LOC…）+ 取前序最近兼容实体；实体共指用相等/互相包含/词重叠>0.7 | coreference_resolver.py:363-376,495-532,630-693 |
| **本体生成** | **from_data 确定性推断** | `OntologyEngine.from_text` → `LLMOntologyGenerator` 独立路径 | 从原始文本直出 ontology JSON（见 §3.3） | engine.py:53-56；llm_generator.py:9-44 |
| 社区摘要 | 抽取式兜底（`llm=None`） | 可选传 llm | 图社区生成自然语言摘要；三层降级 generate_typed→provider.generate_typed→generate_structured | community_summarizer.py:561,1848-1917 |
| LLM 增强后处理 | 不启用 | `enhance_entities/relations` | LLM 校验/补漏已有抽取结果，合并回规则结果 | llm_extraction.py:189,287 |

库内其他 LLM 点位：context/drift_search.py（GraphRAG 式 drift search）、reasoning/graph_reasoner.py（子图上 LLM 推理）、temporal_query_rewriter/temporal_reasoning、LLM 感知分块。

### 3.3 LLM 本体生成路径的实现（朴素，是对照面不是标杆）

`ontology/llm_generator.py` 全文 133 行：单条 prompt（"You are an ontology generator. Read the following text and produce a concise ontology in JSON... Only output valid JSON"，:70-75）+ 内嵌 JSON schema 示例（:58-69），`generate_structured` 一次调用直出，后处理仅做字段名容错归一（Name/name 大小写变体）与补 URI（:77-133）。**没有分片、没有迭代、没有与既有本体/词表的 grounding**——与 from_data 路径的精细度形成鲜明对比，属于辅助性质。

### 3.4 Provider 与 Prompt 工程

- **Provider**：9 个 wrapper——OpenAI、Anthropic、Gemini、Groq、Ollama（本地）、DeepSeek、Novita、HuggingFace 本地 transformers、LiteLLM（聚合 100+ provider）（llms/__init__.py:34-44；providers.py:717-1483）；api_key 走 kwargs 或 `{PROVIDER}_API_KEY` 环境变量。
- **结构化输出**：优先 instructor（默认 Mode.TOOLS；设了 base_url 的第三方网关自动切 Mode.JSON，providers.py:309-358）；无 instructor 时手工 JSON 修复循环（剥 ```json 围栏、去尾逗号、提取首个 JSON 值，:93-281）。
- **Prompt 六条共性**：① 强制 JSON + one-shot 示例；② 防泄漏指令（"Do not include any entities from the example above"，methods.py:1308-1331）；③ 超 token 自动对半分块重试（:1356-1369）；④ prompt 内实体上限 80 个（:2041-2050）；⑤ 结果按 provider/model/文本指纹缓存（:1941-1967）；⑥ **防注入：用户文本先 `json.dumps` 再嵌入**（llm_extraction.py:353-362 注释明确说明）。
- **最有特色的模板**：关系抽取 temporal 变体带 4 个 few-shot + 置信度分档标定——`1.00 = 完整 ISO 日期；0.90 = 年月（"March 2022"）…`，并要求回填 temporal_source_text 原文出处（methods.py:2101-2157）。

---

## 4. 核心算法

### 4.1 本体生成（from_data，默认路径，全确定性）

1. **频率门**：实体 type 出现 ≥ `min_occurrences`（默认 2）才诱导为类；谓词（关系 type）同门（class_inferrer.py:93,161；ontology_generator.py:86-89）。低于阈值的噪声类型直接不进本体。
2. **类名归一 + 冲突硬拒**：PascalCase + 自动单数化（`ies→y`、非 ss 的 `es` 去尾、非白名单去 `s`，naming_conventions.py:386-395；白名单 class/process）；多个源 type 归一后撞名时**直接抛 ValidationError 要求人工改名或显式映射**，不静默合并（class_inferrer.py:159-177）。
3. **公共属性提升**：字段出现在该类 ≥50% 实体上才提升为类属性（Counter + `len(entities)*0.5` 阈值，class_inferrer.py:320-330）；id/type/text/label/confidence 等系统字段排除。
4. **关系端点类型解析**：实体名/ID 别名索引（`build_entity_aliases`）反查 source/target 的类型，关系归入 source_type 概念。
5. **层次构建**：纯命名启发式——类名拆词后逐步去尾找前缀命中（"EmployeeManager" → 去掉 "Manager" 命中 "Employee" 则建 subClassOf，class_inferrer.py:390-404）；兜底挂 Entity/Thing/Resource；**DFS 检测循环继承并报错**（:421-444）。
6. 序列化：rdflib，导出 owl/ttl/rdf/json-ld（ontology_generator.py:122）。

### 4.2 实体去重/消歧（全确定性，无 LLM）

- **多因子加权相似度**：默认 **string 0.6 / property 0.2 / relationship 0.2 / embedding 0.0**（embedding 默认权重为零，仅显式启用才参与），权重自动归一化（deduplication/similarity_calculator.py:95-101,336-342）。
- 字符串算法手写实现可选：Levenshtein DP（:516-547）、Jaro（:568-615）、Jaro-Winkler 前缀加成（:549-566）、字符 bigram Jaccard（:617-633）；默认 jaro_winkler。
- **性能/防误配**：blocking 键 = token 前 4 字符 + type + Soundex 语音键（:651-692）；类型不匹配直接拒、长度比 <0.3 拒、token overlap 门（:162-217）；string_weight>0.5 且 string<0.3 短路（:291-294）；`max_candidates_per_entity` 确定性截断。
- **聚类与合并**：阈值 0.7 / 置信度门槛 0.6（duplicate_detector.py:99-100）；类型显式不同 → 置信度置 0（:775-791）；union-find 聚类；合并策略枚举 KEEP_FIRST/KEEP_LAST/**KEEP_MOST_COMPLETE（默认）**/KEEP_HIGHEST_CONFIDENCE/MERGE_ALL/CUSTOM（merge_strategy.py:54-63,109）；合并写 `metadata.provenance.merged_from` + merge_count 溯源（entity_merger.py:475-520）。

### 4.3 图分析

networkx 生态：环检测（simple_cycles）、孤岛（isolates）、社区检测与层次化、中心度、链接预测、节点嵌入、时态建模/归一/查询重写（kg/ 目录 27 个模块）。

---

## 5. 准确性保障机制

总判断：**确定性规则为主、可单测、CI 友好**（schema_validator.py:16-19 自述 "needs no LLM and is fully unit-testable"）；LLM 只负责内容生成，**质检层零 LLM**。

| # | 机制 | 要点 | 位置 |
|---|---|---|---|
| 1 | 抽取后立即校验 | 置信度 <0.5 告警（high ≥0.8 分桶）、空实体/自环关系报 error、质量分公式 `(1−low×0.5)(1−dup×0.3)(0.5+avg×0.5)`；**confidence=None 放行（"未知≠低质"）** | extraction_validator.py:94,136-165,243-249,290-315；types.py:41-50 |
| 2 | 词表接地（SchemaValidator） | 实体 label 必须落在 schema 概念内否则 out_of_vocabulary error；关系必须满足 domain/range（**精确匹配、不遍历 subClassOf**）；`filter_by_schema` 返回合规子集 | schema_validator.py:71-77,121-146,169-184；schema.py:212-241 |
| 3 | 图结构校验 | 重复 ID=CRITICAL、悬空边=ERROR、自环/环=INFO、孤岛=WARNING（details 只输出前 10 个）；strict 模式 warning 也判无效 | kg/graph_validator.py:105-106,170-177,242-308,314-319 |
| 4 | SHACL 校验 | pyshacl（inference="none"），结果按 sh:resultSeverity 分 violations/warnings/infos；七种约束组件模板化解释，明确 No LLM | ontology_validator.py:148-273,82-135 |
| 5 | 质量门（QualityGate） | 约 20 项 issue code：UNKNOWN_DOMAIN（内置豁免 owl:Thing/rdfs:Resource）、INVALID_DATATYPE_RANGE（range 须在 16 种已知 xsd 类型）、UNKNOWN_RANGE（object property 的 range 须是已声明类）、ORPHAN_CLASS/PROPERTY、MISSING_*_ID 等；门禁默认 `max_errors=0 / min_coverage=0.0 / fail_on_warnings=False`；**告警式门禁：不过门只返回报告不抛异常，阻断与否由消费方决定** | quality_gate.py:93-100,101-126,449-477,542-667 |
| 6 | 频率门 + 人工审批门 | bootstrap 诱导默认 `min_occurrences=2`；属性端点未落在已声明类时降级 owl:Thing；**结果强制 draft=True 永不自动应用**，输出 TTL 供人工审批 | bootstrap_schema.py:9-13,24-58,64,83-85,117-122 |
| 7 | 命名规范校验 | Class=PascalCase+单数+名词短语；object property=camelCase+动词短语（前缀白名单 has/is/can/does/performs/contains/relates）；data property=lowercase/camelCase；违规给修改建议 | naming_conventions.py:321-359,159-163,208-213 |
| 8 | 冲突检测与仲裁 | 五类冲突（VALUE/TYPE/RELATIONSHIP/TEMPORAL/LOGICAL）各有专门检测器；严重度分级（critical 字段/数值差>1000）；七种仲裁策略：多数票（confidence=票数/总数）/可信度加权/MOST_RECENT/FIRST_SEEN/HIGHEST_CONFIDENCE/**MANUAL_REVIEW/EXPERT_REVIEW**——仲裁不了升级人工并打标志，不静默选值 | conflict_detector.py:68-75,120-130,575-613；conflict_resolver.py:102-111,387-424,566-581 |
| 9 | 全链溯源 | W3C PROV-O 兼容，doc→chunk→entity→KG→query→response；每条记录 agent/UTC 时间/置信度/原文引用（"DOI+页码+引文"级）；**SHA-256 + previous_checksum 哈希链防篡改**，verify_chain() 可检出删行 | provenance/schemas.py:80-165,231-292；integrity.py:27-116；manager.py:268-1450 |
| 10 | 本体评估器 | CQ 覆盖率 =answerable/total、完备性（class 须 name+uri+label）、粒度（实例 <2 建议合并 / >1000 建议拆分）、层次健康度（有父类比例 <30% 提示补层次）、类>50 建议拆模块 | ontology_evaluator.py:127-129,176-204,206-237,258-272,320-328 |
| 11 | 本体复用 | 已知目录 FOAF/Dublin Core/Schema.org；`evaluate_alignment` 按命名空间前缀兼容性打分（0.5/0.0 两档，>0.3 判兼容）→ reuse/reject 建议；对齐建议=精确小写 label 匹配生成 owl:equivalentClass 对 | reuse_manager.py:90-109,154-219,241-245,367-412 |

**关键阈值速查**：质量门 max_errors=0（任何 error 不过门）；抽取置信度过滤 0.5（None 放行）；高置信分界 0.8；实体消歧相似度 0.7；dedup 置信度 0.6；共指词重叠 >0.7；类诱导频率门 2；公共属性比例 50%；数值冲突 high 差值>1000；层次建议线 30%。

---

## 6. 文档宣称与源码实现的三处不符

技术画像引用该库宣传能力时，以下三点须以源码为准：

1. **推理机占位**：六阶段第 6 步与文档宣称 "Symbolic Validation → HermiT/Pellet reasoning"（ontology_generator.py:64），但 `OntologyValidator.validate()` 的一致性/可满足性检查是占位 `pass`（ontology_validator.py:340-354，注释 "Placeholder implementation"），`check_constraint` 恒 True。**真正生效的只有 SHACL**。
2. **伪 LLM 选项**：`EventDetector` 构造器签名写 `method="llm"`（event_detector.py:88），`self.method` 存了从未使用（:98），检测实为逐事件正则 `re.finditer`（:331-336）。
3. **复用判定过度简化**：宣传"本体复用治理"，实际 `evaluate_alignment` 只有命名空间前缀 0.5/0.0 两档打分（reuse_manager.py:196-205），URI 加载是占位 warning（:141-146）。

---

## 7. 对本工作台"从物料自动构建本体"的借鉴点

| 借鉴点 | semantica 做法 | 对应本侧 |
|---|---|---|
| 廉价确定性前置过滤 | 频率门（≥2 才成类）+ 公共属性 50% 提升，噪声在进本体前就被滤掉 | 可作为生成断言（D3）/质量门的候选规则；比 LLM 自查便宜且可单测 |
| 生成结果强制 draft 人工审批 | bootstrap_schema 结果永不自动应用（draft=True 硬编码） | 与确认记录表机制同构，可互为佐证 |
| 冲突"升级人工而非静默裁决" | 七种仲裁策略中 MANUAL/EXPERT_REVIEW 打标志不选值 | 与 A1/滞留留痕方向一致：识别不了的显式留给人 |
| 哈希链溯源 | PROV-O 全链 + SHA-256 previous_checksum，可检删行篡改 | 与解析器指纹/口径文档版本号（D3 断言④）同一问题域，实现方式可参考 |
| LLM 输出结构约束 | instructor typed schema + one-shot + 防注入（json.dumps 嵌入）+ 文本指纹缓存 | 生成分片提示词工程的对照组（fusion-jena prompts 之外的第二参照） |
| 时态置信度分档标定 | 1.00=ISO 日期 / 0.90=年月…，要求回填原文出处 | 置信度可解释化（confidence 带标定依据）值得抄 |

**反面教训（不照搬）**：LLM 本体生成路径单 prompt 直出、无接地无迭代，弱于本侧已有的分片+指纹设计；文档宣称与实现脱节（占位推理机/伪选项）说明该库工程广度大于深度，引用其能力表述时必须核对源码。

---

## 附：本文事实来源

- 仓库快照：`/tmp/semantica-research`（浅克隆，2026-09-21，v0.6.8 线）
- 三路并行代码核查（文件解析 / 准确性机制 / 流水线与 LLM 接线）+ `ontology_generator.py`、`class_inferrer.py`、`llm_generator.py` 精读
- 行号对应克隆时点源码，上游持续发版（周度）后可能漂移；引用时建议按类名/函数名定位
- 调研执行：zcode（本轮会话）；共享上下文记录见 `.collaboration/entries/`
