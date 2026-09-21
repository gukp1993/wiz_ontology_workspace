"""从物料自动构建本体：任务域、物料、解析、生成管线与交付。

契约见 文档/接口文档/08-从物料自动构建本体接口.md（唯一口径）。
模块分工：
* protocol.py   共享常量/状态/限额/标识与路径工具（本包唯一口径）
* materials.py  分片上传、blob 存储、限额、ZIP 安全展开（任务附件，非本体存储）
* parsers/      代码/DDL/文档解析适配器 → 带定位的 Fact
* retrieval.py / alignment.py / llm.py / pipeline.py  检索、对齐、抽象、复核
* runner.py     后台有界执行（不持全局写锁），owner 上下文显式绑定
* review.py     候选编辑/决定/合并/撤销/再生成差异
* ontology_adapter.py  候选 → 工作台本体协议（稳定 ID 由交付事务分配）
"""
