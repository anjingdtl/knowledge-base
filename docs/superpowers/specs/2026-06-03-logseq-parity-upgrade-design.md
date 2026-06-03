# Logseq Parity Upgrade Design

> 补齐与 Logseq 的 6 项关键差距，同时保留项目独有的 AI 优势。
> 分三期工程实施，完全向后兼容。

## 背景

ShineHeKnowledge 已采纳 Logseq 的 File-First 架构、Block 大纲模型和 MCP 集成等核心理念，并在 AI 能力（向量搜索、RAG 管线、Wiki 自动编译）上超越了 Logseq。但在以下 6 个维度存在差距：

1. 统一节点模型 — Logseq 将 Page/Block 统一为 Node
2. 标签继承体系 — Logseq 支持标签多继承和属性传播
3. 属性类型系统 — Logseq 有 6 种属性类型和验证
4. 声明式查询语言 — Logseq 使用 Datalog
5. MCP 高级特性 — Logseq 支持 pretend 模式和 undo/redo
6. 双向链接自动发现 — Logseq 自动发现 [[引用]]

## 设计原则

1. **存储层不动** — 现有 `knowledge_items`、`blocks`、`entity_refs` 等表结构不变，通过新增表和适配层扩展
2. **渐进式接入** — 新功能通过可选参数/配置启用，不影响现有 MCP 客户端
3. **服务层扩展** — 新功能全部在 `src/services/` 中实现，通过 `AppContainer` 注入
4. **完全向后兼容** — 现有数据、API、MCP 工具签名不变

## 分期方案

| 期次 | 主题 | 内容 | 依赖 |
|------|------|------|------|
| 第一期 | 地基工程 | 属性类型系统 + 双向链接自动发现 + MCP pretend 模式 + 操作日志 | 无 |
| 第二期 | 图谱增强 | 标签多继承体系 + 统一节点模型（适配器模式） | 第一期属性系统 |
| 第三期 | 查询革命 | 声明式查询 DSL（JSON 格式） | 第二期统一节点 + 标签系统 |

---

## 第一期：地基工程

### 1.1 属性类型系统

#### 新增表

```sql
CREATE TABLE property_schemas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,        -- 'tag' | 'page' | 'global'
    scope_id TEXT,                   -- tag name 或 page_id，global 时为 NULL
    property_name TEXT NOT NULL,     -- 属性名（如 'author', 'priority'）
    property_type TEXT NOT NULL,     -- 'text' | 'number' | 'date' | 'datetime' | 'boolean' | 'url' | 'node_ref'
    required INTEGER DEFAULT 0,     -- 是否必填
    default_value TEXT,              -- 默认值（JSON 序列化）
    choices TEXT,                    -- 可选值列表（JSON 数组）
    constraints TEXT,                -- 额外约束（JSON，如 min/max for number）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope_type, scope_id, property_name)
);
```

#### 新增文件

- `src/services/property_schema.py` — PropertyValidator 服务
- `src/repositories/property_schema_repo.py` — PropertySchemaRepository

#### PropertyValidator 服务接口

```python
class PropertyValidator:
    def define_schema(self, scope_type: str, scope_id: str | None,
                      property_name: str, property_type: str,
                      required: bool = False, default_value: Any = None,
                      choices: list | None = None,
                      constraints: dict | None = None) -> PropertySchema: ...

    def validate_properties(self, scope_type: str, scope_id: str | None,
                            properties: dict) -> ValidationResult: ...

    def get_schema(self, scope_type: str,
                   scope_id: str | None) -> list[PropertySchema]: ...

    def resolve_schema(self, tag_names: list[str],
                       page_id: str | None) -> list[PropertySchema]: ...
```

#### 属性类型验证规则

| 类型 | 验证规则 | 示例值 |
|------|---------|--------|
| `text` | 任意字符串，支持 `[[引用]]` 解析 | `"hello world"` |
| `number` | 整数或浮点数，支持 min/max 约束 | `42`, `3.14` |
| `date` | ISO 8601 日期 `YYYY-MM-DD` | `"2026-06-03"` |
| `datetime` | ISO 8601 日期时间 | `"2026-06-03T10:30:00"` |
| `boolean` | `true` 或 `false` | `true` |
| `url` | 合法 URL 格式 | `"https://example.com"` |
| `node_ref` | 引用 KnowledgeItem 或 Block 的 ID | `"abc-123"` |

#### Schema 解析优先级

```
全局 Schema → 标签 Schema（第二期扩展为继承链） → 页面级 Schema
```

页面级定义覆盖标签级，标签级覆盖全局。

#### 接入点

- `FileGraphService.sync_page()` — 导入时验证 Block properties
- `IndexerService.index_knowledge_item()` — 索引时验证属性
- 验证失败记录 warning 日志，不阻断导入（渐进式类型化）

#### 新增 MCP 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `define_property` | scope_type, scope_id, name, type, required, default, choices | 定义属性 Schema |
| `list_properties` | scope_type, scope_id | 列出属性定义 |

---

### 1.2 双向链接自动发现

#### 新增文件

- `src/services/link_discovery.py` — LinkDiscoveryService

#### 数据库变更

`entity_refs` 表新增字段：

```sql
ALTER TABLE entity_refs ADD COLUMN auto_discovered INTEGER DEFAULT 0;
```

#### LinkDiscoveryService 接口

```python
class LinkDiscoveryService:
    def scan_content(self, content: str, source_id: str,
                     source_type: str) -> list[DiscoveredLink]: ...

    def discover_links(self, knowledge_id: int) -> int: ...

    def discover_all(self) -> dict: ...
```

#### 扫描规则

| 模式 | 匹配规则 | 创建 entity_ref |
|------|---------|----------------|
| `[[Page Title]]` | 按标题匹配 `knowledge_items.title` | source → matched knowledge, ref_type='link' |
| `[[Page Title#Block Content]]` | 匹配特定 Block 内容 | source → matched block, ref_type='link' |
| `#tag_name` | 自动添加到 knowledge_item.tags | 不创建 entity_ref，更新 tags 字段 |

#### 幂等保护

扫描前先删除 `source_id=X AND auto_discovered=1` 的记录，再重新创建。手动创建的引用（`auto_discovered=0`）不受影响。

#### 触发时机

1. `FileGraphService.sync_page()` 完成后自动调用
2. `reindex_all()` 时全量重扫
3. MCP 工具 `discover_links(item_id)` 手动触发

#### 新增 MCP 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `discover_links` | item_id (可选) | 对指定条目或全部执行链接发现 |

---

### 1.3 MCP Pretend 模式 + 操作日志

#### 新增表

```sql
CREATE TABLE operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,    -- 'create' | 'update' | 'delete' | 'ingest' | 'wiki_save' | 'reindex'
    target_type TEXT NOT NULL,       -- 'knowledge' | 'block' | 'wiki_page' | 'entity_ref'
    target_id TEXT,                  -- 操作目标 ID
    actor TEXT DEFAULT 'mcp',       -- 'mcp' | 'api' | 'system'
    params TEXT,                     -- 操作参数（JSON）
    before_snapshot TEXT,            -- 操作前快照（JSON）
    after_snapshot TEXT,             -- 操作后快照（JSON）
    status TEXT DEFAULT 'completed', -- 'pretended' | 'completed' | 'failed'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 新增文件

- `src/services/operation_log.py` — OperationLogService
- `src/repositories/operation_log_repo.py` — OperationLogRepository

#### Pretend 模式实现

所有写操作的 MCP 工具新增可选参数 `dry_run: bool = False`：

```python
@mcp_tool
async def create(title: str, content: str, ..., dry_run: bool = False):
    if dry_run:
        result = simulate_create(title, content, ...)
        log_operation('create', status='pretended', params=..., after_snapshot=result)
        return {"pretend": True, "would_create": result}
    else:
        result = actual_create(...)
        log_operation('create', status='completed', params=..., after_snapshot=result)
        return result
```

#### 受影响的 MCP 工具

| 工具 | dry_run 行为 |
|------|-------------|
| `create` | 验证参数和 Schema，返回 would_create 结果 |
| `update` | 验证参数，返回 before/after diff |
| `delete` | 返回 would_delete 的目标及关联数据 |
| `ingest_file` | 解析文件但不写入，返回 would_create 的条目和 Block 数 |
| `ingest_url` | 抓取 URL 但不写入，返回解析结果 |
| `save_to_wiki` | 模拟 Wiki 编译，返回 would_create 的页面 |
| `reindex_all` | 返回当前索引统计，不执行重建 |

#### 新增 MCP 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `operation_history` | limit=20, operation_type=None, status=None | 查询操作日志 |

---

## 第二期：图谱增强

### 2.1 标签多继承体系

#### 新增表

```sql
CREATE TABLE tag_hierarchy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_name TEXT NOT NULL,
    parent_tag TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tag_name, parent_tag)
);

CREATE TABLE tag_property_defs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_name TEXT NOT NULL,
    property_name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    default_value TEXT,
    required INTEGER DEFAULT 0,
    UNIQUE(tag_name, property_name)
);
```

#### 新增文件

- `src/services/tag_hierarchy.py` — TagHierarchyService
- `src/repositories/tag_hierarchy_repo.py` — TagHierarchyRepository

#### TagHierarchyService 接口

```python
class TagHierarchyService:
    def add_parent(self, tag_name: str, parent_tag: str) -> None: ...
    def remove_parent(self, tag_name: str, parent_tag: str) -> None: ...
    def get_ancestors(self, tag_name: str) -> list[str]: ...
    def get_descendants(self, tag_name: str) -> list[str]: ...
    def get_tree(self) -> dict: ...
    def get_inherited_properties(self, tag_name: str) -> list[PropertySchema]: ...
    def resolve_tags(self, tag_names: list[str]) -> TagResolution: ...
```

#### 属性传播规则

1. 子标签自动继承所有祖先标签的 `tag_property_defs`
2. 同名属性子标签覆盖父标签（最近优先）
3. 多继承冲突时按 `add_parent` 创建顺序决定优先级（先添加的优先）
4. 查询带某标签的节点时，自动包含所有后代标签的节点

#### DAG 约束

`add_parent()` 执行前检测环：如果 `parent_tag` 的祖先链中已包含 `tag_name`，拒绝操作并返回错误。

#### 与第一期属性系统集成

`PropertyValidator.resolve_schema()` 升级为：

```
全局 Schema → 标签继承 Schema（合并所有祖先 tag_property_defs） → 页面级 Schema
```

#### 新增 MCP 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `add_tag_parent` | tag_name, parent_tag | 建立标签继承关系 |
| `remove_tag_parent` | tag_name, parent_tag | 移除继承关系 |
| `get_tag_tree` | (无) | 获取完整标签层级树 |
| `define_tag_property` | tag_name, property_name, property_type, ... | 定义标签级属性 |

---

### 2.2 统一节点模型（适配器模式）

#### 新增文件

- `src/models/node.py` — NodeProtocol + KnowledgeItemAdapter + BlockAdapter
- `src/services/node_service.py` — NodeService

#### NodeProtocol

```python
class NodeProtocol(Protocol):
    @property
    def node_id(self) -> str: ...
    @property
    def node_type(self) -> Literal['page', 'block']: ...
    @property
    def title(self) -> str: ...
    @property
    def content(self) -> str: ...
    @property
    def tags(self) -> list[str]: ...
    @property
    def properties(self) -> dict: ...
    @property
    def parent_node_id(self) -> str | None: ...
    @property
    def child_node_ids(self) -> list[str]: ...
    @property
    def linked_node_ids(self) -> list[str]: ...
```

#### 适配器

```python
class KnowledgeItemAdapter:
    """KnowledgeItem → NodeProtocol"""
    node_type = 'page'
    parent_node_id = None  # Page 无父节点
    child_node_ids = [block.id for block in blocks]  # 顶级 Block
    linked_node_ids = [ref.target_id for ref in entity_refs]

class BlockAdapter:
    """Block → NodeProtocol"""
    node_type = 'block'
    parent_node_id = block.parent_id or page.id
    child_node_ids = [child.id for child in children]
    linked_node_ids = [ref.target_id for ref in entity_refs]
```

#### NodeService 接口

```python
class NodeService:
    def get_node(self, node_id: str) -> NodeProtocol: ...
    def get_nodes(self, node_ids: list[str]) -> list[NodeProtocol]: ...
    def query_nodes(self, tags=None, properties=None,
                    node_type=None, limit=20, offset=0) -> list[NodeProtocol]: ...
    def get_references(self, node_id: str) -> list[NodeProtocol]: ...
    def get_ancestors(self, node_id: str) -> list[NodeProtocol]: ...
    def get_descendants(self, node_id: str) -> list[NodeProtocol]: ...
    def set_property(self, node_id: str, key: str, value: Any) -> None: ...
    def add_tag(self, node_id: str, tag: str) -> None: ...
    def remove_tag(self, node_id: str, tag: str) -> None: ...
```

#### 新增 MCP 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `get_node` | node_id | 统一获取节点（自动判断 Page/Block） |
| `query_nodes` | tags, properties, node_type, limit, offset | 统一查询 |
| `get_node_graph` | node_id, depth=2 | 获取节点图谱邻域 |

#### 与现有系统兼容

- 现有 MCP 工具（`search`, `create`, `read` 等）保持不变
- `NodeService` 作为新的高层抽象，内部调用现有 Repository
- 第三期查询 DSL 基于 `NodeService` 构建

---

## 第三期：查询革命

### 3.1 声明式查询 DSL

#### 新增文件

- `src/services/query_dsl.py` — QueryEngine + QueryValidator + QueryCompiler
- `src/models/query.py` — 查询 DSL 数据模型

#### 查询语法

```json
{
    "from": "nodes",
    "where": {
        "tags": {"any": ["AI", "ML"]},
        "properties": {
            "priority": {"eq": "high"},
            "created_at": {"gte": "2025-01-01"}
        },
        "content": {"contains": "transformer"},
        "type": "page"
    },
    "traverse": {
        "direction": "outgoing",
        "ref_types": ["link", "embed"],
        "depth": 2,
        "tags": ["Concept"]
    },
    "select": ["title", "tags", "properties.priority"],
    "order_by": {"field": "created_at", "direction": "desc"},
    "limit": 20,
    "offset": 0
}
```

#### 过滤操作符

| 类别 | 操作符 | 说明 |
|------|--------|------|
| 通用 | `eq`, `neq`, `in`, `not_in` | 等值/集合匹配 |
| 数值/日期 | `gt`, `gte`, `lt`, `lte` | 范围比较 |
| 文本 | `contains`, `starts_with`, `ends_with`, `matches` | 文本匹配（`matches` 为正则） |
| 标签 | `any`, `all`, `none` | 标签集合匹配 |
| 逻辑 | `and`, `or`, `not` | 条件组合（嵌套 where） |

#### QueryEngine 接口

```python
class QueryEngine:
    def execute(self, query: dict) -> QueryResult: ...
    def validate(self, query: dict) -> list[str]: ...
    def explain(self, query: dict) -> str: ...
```

#### 执行流程

```
JSON 查询 → 语法验证 → SQL 编译 → 执行 → 结果组装
                                        ↓
                              通过 NodeService 返回
                              统一 NodeProtocol 列表
```

#### SQL 编译策略

| DSL 特性 | SQL 实现 |
|----------|---------|
| 属性过滤 | `JOIN blocks ON properties JSON_EXTRACT` |
| 标签过滤 | `tag_hierarchy` 展开后代 → `IN` 子句 |
| 关系遍历 | 递归 CTE 查询 `entity_refs` |
| 排序/分页 | `ORDER BY` + `LIMIT`/`OFFSET` |

#### 安全限制

- 单次查询最大 `depth=5`
- `limit` 上限 1000
- 正则 `matches` 操作符 2 秒超时
- 递归 CTE 最大迭代次数 10

#### 新增 MCP 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `query` | query_json | 执行 DSL 查询 |
| `query_explain` | query_json | 返回查询执行计划 |

#### 新增 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/query` | POST | 执行 DSL 查询 |
| `/api/query/validate` | POST | 验证查询语法 |
| `/api/query/explain` | POST | 查询执行计划 |

---

## 新增文件汇总

### 第一期

```
src/services/property_schema.py
src/services/link_discovery.py
src/services/operation_log.py
src/repositories/property_schema_repo.py
src/repositories/operation_log_repo.py
alembic/versions/xxx_phase1_foundation.py
tests/test_property_schema.py
tests/test_link_discovery.py
tests/test_operation_log.py
```

### 第二期

```
src/models/node.py
src/services/tag_hierarchy.py
src/services/node_service.py
src/repositories/tag_hierarchy_repo.py
alembic/versions/xxx_phase2_graph.py
tests/test_tag_hierarchy.py
tests/test_node_service.py
```

### 第三期

```
src/models/query.py
src/services/query_dsl.py
alembic/versions/xxx_phase3_query.py
tests/test_query_dsl.py
```

## 新增数据库表汇总

| 表名 | 期次 | 用途 |
|------|------|------|
| `property_schemas` | 第一期 | 属性类型定义 |
| `operation_logs` | 第一期 | 写操作日志 |
| `tag_hierarchy` | 第二期 | 标签父子关系（DAG） |
| `tag_property_defs` | 第二期 | 标签级属性定义 |

## 新增 MCP 工具汇总

| 工具 | 期次 | 类型 | 说明 |
|------|------|------|------|
| `define_property` | 第一期 | 写 | 定义属性 Schema |
| `list_properties` | 第一期 | 只读 | 列出属性定义 |
| `discover_links` | 第一期 | 写 | 触发链接发现 |
| `operation_history` | 第一期 | 只读 | 查询操作日志 |
| `add_tag_parent` | 第二期 | 写 | 建立标签继承 |
| `remove_tag_parent` | 第二期 | 写 | 移除标签继承 |
| `get_tag_tree` | 第二期 | 只读 | 获取标签树 |
| `define_tag_property` | 第二期 | 写 | 定义标签属性 |
| `get_node` | 第二期 | 只读 | 统一获取节点 |
| `query_nodes` | 第二期 | 只读 | 统一查询节点 |
| `get_node_graph` | 第二期 | 只读 | 节点图谱邻域 |
| `query` | 第三期 | 只读 | DSL 查询 |
| `query_explain` | 第三期 | 只读 | 查询执行计划 |

## 现有 MCP 工具变更

| 工具 | 变更 | 期次 |
|------|------|------|
| `create` | 新增 `dry_run` 参数 | 第一期 |
| `update` | 新增 `dry_run` 参数 | 第一期 |
| `delete` | 新增 `dry_run` 参数 | 第一期 |
| `ingest_file` | 新增 `dry_run` 参数 | 第一期 |
| `ingest_url` | 新增 `dry_run` 参数 | 第一期 |
| `save_to_wiki` | 新增 `dry_run` 参数 | 第一期 |
| `reindex_all` | 新增 `dry_run` 参数 | 第一期 |

## 成功标准

### 第一期

- 属性 Schema 可定义、可验证，验证失败不阻断导入
- `[[引用]]` 自动发现并写入 entity_refs，幂等可重跑
- 所有写操作支持 `dry_run` 预览，操作日志可查询

### 第二期

- 标签支持多继承 DAG，无环检测
- 属性沿标签继承链传播，子标签覆盖父标签
- NodeService 统一查询 Page 和 Block，返回一致的 NodeProtocol

### 第三期

- JSON DSL 可执行属性过滤、标签过滤、关系遍历
- 查询结果通过 NodeService 返回统一格式
- 安全限制生效（depth/limit/timeout）
