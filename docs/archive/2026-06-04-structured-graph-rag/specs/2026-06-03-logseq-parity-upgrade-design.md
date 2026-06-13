# Structured/Graph RAG Upgrade Design

> 第一目标从“补齐 Logseq parity”调整为“先补齐 Structured/Graph RAG 的召回质量闭环”。
> Logseq 的 Page/Block/Link/Property 能力仍是基础，但首期优先服务 RAG 准确率、可解释性和溯源体验。

## 背景

ShineHeKnowledge 已具备 File-First、Block 大纲、向量检索、RAG 管线、Wiki 编译和图谱展示能力。原方案聚焦补齐 Logseq 的 6 项差距：统一节点、标签继承、属性类型、声明式查询、MCP pretend/undo、双向链接发现。

桌面优化文档指出了更直接影响产品效果的 RAG 实战差距：

1. **Parent-Child Retriever**：命中小 Block 后，生成上下文应扩展到父链和相邻兄弟 Block。
2. **Contextual Headers**：Embedding 和 RAG 输入应保留结构化标题/父级语境，但不能污染原始 `blocks.content`。
3. **Link Expansion**：`[[Page]]` / `[[Page#Block]]` 不只写图谱边，还要在 RAG 上下文中展开目标摘要。
4. **Query Router**：强逻辑查询不能只走向量相似度，应优先转为结构化属性/标签/链接查询。
5. **Source Graph**：问答结果除文本答案和 sources 外，应返回命中节点与关系图，支持“文本 + 图谱 + 溯源”。

## 设计原则

1. **RAG 质量优先**：首期所有改动必须能改善召回、上下文完整性、结构化查询或溯源。
2. **存储兼容**：继续使用 SQLite/sqlite-vec，不引入 Milvus、Qdrant 或图数据库。
3. **原文与上下文分离**：`blocks.content` 保存原始 Block 文本；父链、兄弟、链接摘要只在检索/RAG 阶段动态拼接。
4. **渐进增强**：现有 API、MCP 工具和 `sources` 返回保持兼容，新能力通过附加字段和服务接入。
5. **Block-first**：Page、Block、EntityRef 都服务于 Block 级召回和可追溯来源。

## 分期方案

| 期次 | 主题 | 内容 |
| --- | --- | --- |
| 第一期 | Structured/Graph RAG 地基 | 结构化导入、父子/兄弟上下文、双向链接发现与展开、规则型 Query Router、问答 `source_graph` |
| 第二期 | Logseq 图谱能力 | 统一节点模型、标签多继承、属性类型 Schema、属性传播 |
| 第三期 | 查询革命 | 完整 JSON DSL、自然语言到 DSL/SQL/Graph Query 的 Agentic Router、查询解释 |
| 独立计划 | 操作安全 | MCP `dry_run`、操作日志、undo/redo |

## 第一期：Structured/Graph RAG 地基

### 1. 结构化导入闭环

文件解析器产出的 `ParsedFile.structured` 必须优先进入 `FileGraphService.create_page()`，无结构化结果时才回退 `ParsedFile.content`。

接入点：

- MCP `ingest_file`
- GUI 文件导入
- 后续 API 文件导入入口如出现，也必须使用同一规则

成功标准：

- Excel/CSV/PDF/DOCX/PPT 的层级 Block 写入 `blocks.parent_id`
- `knowledge_chunks` 可继续兼容旧索引，但不得覆盖结构化 `blocks.parent_id`

### 2. Small-to-Big Block Context

正式化 `BlockContextService`：

- 父链：最多 `rag.context_trace_depth`
- 当前命中 Block
- 相邻兄弟：窗口 `rag.context_sibling_window`
- 链接目标摘要：最多 `rag.link_expansion.max_links`

成功标准：

- Hybrid/keyword/vector 命中子 Block 后，RAG context 包含父级与相邻兄弟语境
- 上下文动态拼接，不写回 `blocks.content`

### 3. 双向链接发现与链接展开

`LinkDiscoveryService` 扫描 Block 内容：

- `[[Page Title]]` -> `entity_refs(source_type='block', target_type='knowledge', ref_type='link')`
- `[[Page Title#Block Content]]` -> 指向目标 Block

成功标准：

- 链接写入 `entity_refs`
- RAG 上下文能通过 `entity_refs` 展开目标 Page/Block 摘要

### 4. 轻量 Query Router

第一期只做规则型路由，不引入 LLM Agent。

逻辑查询识别：

- `#tag`
- `::property value`
- `[[Page]]`
- 包含“所有 / 状态 / 属于 / 筛选 / 查找 / 找出”等强逻辑信号

执行策略：

- 逻辑查询：走 SQLite 结构化查询，组合 `knowledge_items.tags`、`block_property_index`、`entity_refs`
- 模糊总结：继续走 Hybrid Search + Rerank

成功标准：

- `#bug ::status unresolved [[前端重构]]` 类问题不调用向量检索即可返回精确 Block

### 5. Source Graph Payload

RAG 同步问答结果新增：

```json
{
  "answer": "...",
  "sources": [],
  "source_graph": {
    "nodes": [],
    "edges": []
  }
}
```

兼容要求：

- `sources` 字段保持不变
- MCP `ask` 自动透传
- API `/chat/ask` 返回 `source_graph`

## 第二期：Logseq 图谱能力

保留原 Logseq parity 的能力，但顺序调整到 RAG 地基之后：

- 统一节点模型：Page/Block 通过适配器返回一致 Node 视图
- 标签多继承：DAG、无环检测、后代查询展开
- 属性类型 Schema：`text/number/date/datetime/boolean/url/node_ref`
- 属性传播：全局 -> 标签继承链 -> 页面级覆盖

## 第三期：查询革命

在第一期 Query Router 基础上扩展：

- JSON DSL
- 查询验证、解释、限制
- 自然语言到 DSL/SQL/Graph Query 的 Agentic Router
- 多跳关系遍历和可解释执行计划

## 独立计划：操作安全

以下能力仍有价值，但不进入第一期主线：

- MCP `dry_run`
- `operation_logs`
- undo/redo
- 写操作差异预览

这些能力应单独设计，避免挤占 RAG 质量优化的首期节奏。
