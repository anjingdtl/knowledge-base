# ShineHeKnowledge 第三阶段工程规格：Canonical Wiki、Claim 级溯源与自动失效传播

- **文档状态**：Proposed / 可进入编码
- **目标仓库**：`anjingdtl/knowledge-base`
- **基线版本**：`v1.5.2`
- **建议目标版本**：`v1.6.0`
- **建议存放路径**：`docs/superpowers/specs/2026-07-07-canonical-wiki-claim-provenance-design.md`
- **适用执行者**：Claude Code、Codex、Cursor Agent、Cline 等具备仓库读写与测试能力的编码 Agent
- **核心主题**：统一 Wiki 主数据源、Claim 级证据链、跨来源知识合并、来源变更后的级联重编译
- **前置阶段**：Wiki-First 第一阶段、检索执行层第二阶段、双轨 Wiki 轻量收敛

---

## 1. 执行摘要

当前 ShineHeKnowledge 已具备以下能力：

- 本地优先的多格式文档摄取；
- SQLite、FTS5、sqlite-vec 混合检索；
- RRF、rerank、parent-child 与结构化引用；
- `raw/`、`wiki/`、`schema/`、`artifacts/` 目录契约；
- `index.md`、`log.md`、Wiki-first 路由；
- 查询答案回写、Wiki lint、版本与审批工作流；
- 文件系统 Wiki 与 SQLite Wiki 的轻量双写和互补读取。

当前最大架构问题不是检索能力不足，而是：

1. SQLite `wiki_pages` 与文件系统 `wiki/*.md` 仍是两个独立知识系统；
2. Wiki 页面只有页级 `source_ids`，缺少 Claim 级 block/location 证据；
3. 实体页更新仍偏“整页生成或重写”，没有稳定的跨来源 Claim 合并模型；
4. 来源更新、删除后只能检测部分过期状态，不能自动计算影响范围并重编译；
5. 查询回写主要是“保存答案”，尚未形成去重、合并、纠错和晋级的学习闭环。

本 Spec 将系统收敛为以下目标架构：

```text
raw/ 原始资料（不可变 source of truth）
        │
        ▼
Canonical Markdown Wiki（唯一权威知识层）
        │
        ├── 页面正文与人工可审阅元数据
        ├── Claim 结构化事实
        ├── Claim → Evidence 精细证据链
        └── Page/Claim/Source 依赖关系
        │
        ▼
SQLite Projection（可删除、可重建的查询投影）
        │
        ├── FTS / 向量索引
        ├── Page / Claim / Evidence 表
        ├── Link / Dependency 图
        └── MCP、REST、GUI 查询接口
```

**关键决策：文件系统 Markdown Wiki 是唯一 canonical store；SQLite 是从 Markdown 和 raw 索引派生的 projection，不再独立生产第二套 Wiki 内容。**

---

## 2. 背景与现状

### 2.1 当前双轨结构

当前系统存在两套 Wiki 产物：

- **A 轨：SQLite Wiki**
  - 主要入口：`WikiCompiler`
  - 主要存储：`wiki_pages`、`wiki_links`、`wiki_ops`
  - 优势：工作流、版本、图关系、现有 GUI/API 支持较完整
  - 问题：与文件系统 Wiki 内容、主键、状态和链接不统一

- **B 轨：文件系统 Wiki**
  - 主要入口：`KnowledgeWorkflowService`
  - 主要存储：`wiki/sources`、`entities`、`concepts`、`comparisons`、`syntheses`
  - 优势：符合 Wiki-first、人类可审阅、可由 Git 管理
  - 问题：缺稳定 page ID、Claim 数据模型、统一 workflow 和完整依赖传播

`WikiWriteService` 当前只是将一次写入分发到两轨；任一失败不阻塞另一轨。该设计解决了入口散乱，但仍允许两轨内容、状态和链接长期漂移。

### 2.2 当前实体更新限制

`WikiEntityUpdater` 当前特征：

- 每次 ingest 最多调用若干次 LLM；
- 根据 `key_entities` 更新实体或概念页；
- 读取已有页面的有限上下文；
- 输出 summary、facts、contradictions；
- 页面 frontmatter 已包含 `source_ids`，但没有 Claim 级证据对象；
- 页面正文中的事实只是普通 Markdown 列表。

该实现适合作为第一阶段原型，但无法稳定完成：

- 一个事实由多个来源共同支持；
- 新来源修正旧事实而不是覆盖整页；
- 冲突事实并存并等待裁决；
- 来源删除后判断某个 Claim 是否仍有其他证据；
- 每条事实回到具体 block、页码、标题路径或表格位置。

### 2.3 当前 lint 与失效传播限制

当前 lint 能发现孤儿页、重复页、死链、无 backlinks、来源删除、source hash 变化和部分矛盾，但仍属于“检测层”。

目标系统必须形成：

```text
来源变更
→ 定位受影响 Evidence
→ 定位受影响 Claim
→ 定位受影响 Page
→ 生成重编译计划
→ 在 staging 中重编译
→ 输出 diff
→ 自动发布或进入 review
→ 重建 SQLite projection
```

---

## 3. 目标与非目标

## 3.1 目标

### G1：建立唯一 Canonical Wiki

- 文件系统 `wiki/*.md` 与结构化 Claim 文件是唯一权威源；
- SQLite 中的 Wiki 数据全部可由 canonical 文件重建；
- 所有写入口必须经过统一 Repository；
- 旧 `WikiCompiler` 不再直接写独立 Wiki 数据，只作为兼容适配器。

### G2：实现稳定 Page ID

- 每个 Wiki 页面拥有不可随标题和路径变化的 UUID；
- slug、文件名、标题可变，但 `page_id` 不变；
- 重命名、移动页面不得产生新页面身份；
- SQLite projection 以 `page_id` 为主键。

### G3：实现 Claim 级证据链

每条可验证事实必须具备：

- `claim_id`；
- 事实陈述；
- Claim 状态；
- 证据列表；
- 每条证据的 `knowledge_id`、`block_id`、location、source revision；
- 支持、反驳或替代关系；
- 可信度与时间范围。

### G4：实现跨来源 Claim 合并

新来源进入后，系统应将抽取到的 Claim 分类为：

- `new`：新增事实；
- `supports`：为现有事实增加证据；
- `refines`：补充更精确表述；
- `contradicts`：与现有事实冲突；
- `supersedes`：新事实替代旧事实；
- `duplicate`：语义重复，不新增；
- `unresolved`：无法可靠判断，进入 review。

### G5：实现来源变更级联重编译

- 来源内容 hash 或 block revision 变化后，自动找出受影响 Claim/Page；
- 来源删除后，移除对应 Evidence，而不是直接删除整条 Claim；
- Claim 无剩余有效证据时改为 `unsupported` 或 `retracted`；
- 被影响页面进入 draft/review，禁止静默保持 published。

### G6：将查询回写升级为学习闭环

高价值问答保存时：

- 先检索现有页面；
- 判断新增页、合并页或不保存；
- 将答案拆解成 Claim；
- 将 Claim 与已有事实合并；
- 保存查询来源和生成上下文；
- 支持用户反馈修正 Claim；
- 通过规则生成 publish 建议，不直接无条件发布。

### G7：保持现有检索与客户端兼容

- `search`、`ask`、`read` 等核心 MCP 工具契约不破坏；
- 现有 `wiki_first` 项目可迁移；
- `legacy` 模式不受影响；
- 迁移期间保留 fallback，至少跨一个 minor 版本。

---

## 3.2 非目标

本阶段明确不做：

- 更换 sqlite-vec、FTS5 或向量数据库；
- 更换 embedding 模型或解决所有维度迁移问题；
- 重写前端；
- 重构用户认证和 RBAC；
- 引入分布式消息队列；
- 实现多人实时协同编辑；
- 将 Wiki 发布成完整静态站点；
- 解决通用世界知识真实性问题；
- 让 Agent 自动裁决所有冲突；
- 删除旧 SQLite Wiki 表。

旧表和兼容代码只标记 deprecated，本阶段不得破坏性删除。

---

## 4. 架构决策

## 4.1 ADR-001：Markdown 为 Canonical Store

### 决策

Canonical Wiki 由以下内容组成：

```text
wiki/
├── index.md
├── log.md
├── sources/
├── entities/
├── concepts/
├── comparisons/
├── syntheses/
├── claims/
│   └── <claim_id>.yaml
├── _meta/
│   ├── pages.json
│   ├── redirects.json
│   └── schema-version
└── _staging/
```

- 页面正文继续使用 Markdown；
- Claim 使用单独的 YAML 文件；
- 页面通过 frontmatter 的 `claim_ids` 引用 Claim；
- 页面正文允许使用 `<!-- claim:<claim_id> -->` 作为稳定锚点；
- SQLite 只保存 projection；
- Git diff 即知识修改 diff。

### 理由

- 保持本地优先与人工可审阅；
- 避免超大 frontmatter；
- Claim 可独立复用在多个页面；
- 页面与 Claim 可分别版本化；
- 迁移与回滚更透明；
- 能将 SQLite 视为可重建缓存和查询层。

---

## 4.2 ADR-002：所有写操作经过 WikiRepository

禁止下列服务直接调用 `write_markdown` 或 `Database.insert_wiki_page` 创建 canonical 知识：

- `WikiCompiler`
- `KnowledgeWorkflowService`
- `WikiEntityUpdater`
- `WikiWriteService`
- MCP handler
- API route
- GUI worker

统一入口：

```python
class WikiRepository(Protocol):
    def get_page(self, page_id: str) -> WikiPage | None: ...
    def get_page_by_title(self, title: str) -> WikiPage | None: ...
    def list_pages(self, page_type: str | None = None) -> list[WikiPage]: ...
    def save_page(self, page: WikiPage, expected_revision: int | None = None) -> SaveResult: ...
    def move_page(self, page_id: str, new_title: str, new_page_type: str | None = None) -> SaveResult: ...
    def get_claim(self, claim_id: str) -> Claim | None: ...
    def save_claim(self, claim: Claim, expected_revision: int | None = None) -> SaveResult: ...
    def delete_claim(self, claim_id: str, soft: bool = True) -> SaveResult: ...
    def transaction(self) -> ContextManager[WikiTransaction]: ...
```

Repository 负责：

- schema 校验；
- revision 乐观锁；
- 原子写；
- page/claim ID；
- staging；
- redirect；
- operation log；
- projection outbox。

---

## 4.3 ADR-003：投影使用 Outbox + 幂等重建

Canonical 文件写成功后写入本地 outbox：

```text
data/wiki_projection_outbox.jsonl
```

事件类型：

- `page.created`
- `page.updated`
- `page.moved`
- `page.deprecated`
- `claim.created`
- `claim.updated`
- `claim.status_changed`
- `evidence.added`
- `evidence.removed`
- `projection.rebuild_requested`

Projection worker 必须：

- 事件幂等；
- 允许重复消费；
- 失败可重试；
- 可通过 `shinehe wiki sync-index` 全量修复；
- canonical 写成功不得因 projection 失败回滚；
- projection 延迟必须出现在 health 状态中。

---

## 5. Canonical 数据模型

## 5.1 WikiPageV2

页面 frontmatter：

```yaml
schema_version: 2
page_id: "page_550e8400-e29b-41d4-a716-446655440000"
title: "FTTR"
page_type: "concepts"
status: "draft"
revision: 7
aliases:
  - "全光组网"
tags:
  - "宽带"
  - "网络"
source_ids:
  - "knowledge_xxx"
claim_ids:
  - "claim_xxx"
created_at: "2026-07-07T12:00:00+08:00"
updated_at: "2026-07-07T13:00:00+08:00"
content_hash: "sha256:..."
supersedes_page_id: null
```

字段要求：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `schema_version` | 是 | 固定为 `2` |
| `page_id` | 是 | 稳定 UUID，路径变化不得改变 |
| `title` | 是 | 人类标题 |
| `page_type` | 是 | `sources/entities/concepts/comparisons/syntheses` |
| `status` | 是 | `draft/review/published/deprecated` |
| `revision` | 是 | 每次 canonical 修改加 1 |
| `aliases` | 否 | 历史标题和同义标题 |
| `tags` | 否 | 标签 |
| `source_ids` | 是 | 页面直接或间接关联来源合集 |
| `claim_ids` | 是 | 页面引用的 Claim |
| `created_at` | 是 | 首次创建时间 |
| `updated_at` | 是 | 最近修改时间 |
| `content_hash` | 是 | 正文与关键 frontmatter 的 hash |
| `supersedes_page_id` | 否 | 页面级替代关系 |

### 页面正文规则

```markdown
# FTTR

## Summary

FTTR 是一种面向家庭或小微场景的全光组网方案。

## Facts

<!-- claim:claim_01 -->
- FTTR 使用光纤延伸至房间级节点。

<!-- claim:claim_02 -->
- 主从网关共同完成室内覆盖。

## Open Questions

- 不同厂商互通性仍需依据具体协议与版本判断。
```

---

## 5.2 ClaimV1

`wiki/claims/<claim_id>.yaml`：

```yaml
schema_version: 1
claim_id: "claim_550e8400-e29b-41d4-a716-446655440000"
statement: "FTTR 使用光纤延伸至房间级节点。"
normalized_statement: "fttr使用光纤延伸至房间级节点"
claim_type: "fact"
status: "active"
confidence: 0.91
valid_from: null
valid_to: null
subject_refs:
  - "entity:FTTR"
predicate: "uses"
object_refs:
  - "concept:fiber-room-node"
evidence:
  - evidence_id: "ev_xxx"
    stance: "supports"
    knowledge_id: "knowledge_xxx"
    block_id: "block_xxx"
    location:
      page: 8
      heading_path:
        - "技术方案"
        - "组网结构"
      paragraph_index: 2
    source_revision: "sha256:..."
    excerpt_hash: "sha256:..."
    observed_at: "2026-07-07T12:00:00+08:00"
relations:
  - relation: "refines"
    target_claim_id: "claim_old"
created_at: "2026-07-07T12:00:00+08:00"
updated_at: "2026-07-07T13:00:00+08:00"
revision: 3
```

### Claim 状态

| 状态 | 含义 |
|---|---|
| `active` | 当前有有效支持证据 |
| `disputed` | 存在互相冲突且未裁决的证据 |
| `superseded` | 已被更精确或更新 Claim 替代 |
| `unsupported` | 所有支持证据已失效或删除 |
| `retracted` | 人工确认不应继续使用 |
| `draft` | 新抽取但尚未完成校验 |

### Evidence stance

- `supports`
- `contradicts`
- `qualifies`
- `supersedes`

### 强制约束

- `published` 页面不得引用 `draft` Claim；
- `active` Claim 至少有一条有效 `supports` Evidence；
- Evidence 必须包含 `knowledge_id`；
- 能定位 block 时必须写 `block_id`；
- 解析器能提供 location 时不得丢弃 location；
- `source_revision` 必须对应摄取时的 source hash；
- `confidence` 只能作为排序和 review 信号，不能替代证据。

---

## 5.3 Page Registry

`wiki/_meta/pages.json` 保存稳定映射：

```json
{
  "page_...": {
    "path": "concepts/fttr.md",
    "title": "FTTR",
    "page_type": "concepts",
    "revision": 7,
    "content_hash": "sha256:..."
  }
}
```

用途：

- page ID 到路径解析；
- 页面改名；
- redirect；
- 冲突检测；
- 快速全量扫描；
- migration parity 校验。

该文件由 Repository 自动维护，Agent 业务代码不得手写。

---

## 6. SQLite Projection 数据模型

新增 Alembic migration，不删除旧表。

建议新增表：

### `wiki_pages_v2`

- `page_id TEXT PRIMARY KEY`
- `path TEXT UNIQUE NOT NULL`
- `title TEXT NOT NULL`
- `page_type TEXT NOT NULL`
- `status TEXT NOT NULL`
- `revision INTEGER NOT NULL`
- `content TEXT NOT NULL`
- `content_hash TEXT NOT NULL`
- `aliases_json TEXT NOT NULL`
- `tags_json TEXT NOT NULL`
- `source_ids_json TEXT NOT NULL`
- `claim_ids_json TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

### `wiki_claims`

- `claim_id TEXT PRIMARY KEY`
- `statement TEXT NOT NULL`
- `normalized_statement TEXT NOT NULL`
- `claim_type TEXT NOT NULL`
- `status TEXT NOT NULL`
- `confidence REAL NOT NULL`
- `valid_from TEXT`
- `valid_to TEXT`
- `revision INTEGER NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

### `wiki_claim_evidence`

- `evidence_id TEXT PRIMARY KEY`
- `claim_id TEXT NOT NULL`
- `stance TEXT NOT NULL`
- `knowledge_id TEXT NOT NULL`
- `block_id TEXT`
- `location_json TEXT NOT NULL`
- `source_revision TEXT NOT NULL`
- `excerpt_hash TEXT`
- `observed_at TEXT NOT NULL`
- 唯一约束：`claim_id + knowledge_id + block_id + stance + source_revision`

### `wiki_page_claims`

- `page_id TEXT NOT NULL`
- `claim_id TEXT NOT NULL`
- `display_order INTEGER NOT NULL`
- 主键：`page_id + claim_id`

### `wiki_dependencies`

- `from_type TEXT NOT NULL`
- `from_id TEXT NOT NULL`
- `to_type TEXT NOT NULL`
- `to_id TEXT NOT NULL`
- `relation TEXT NOT NULL`
- 主键：上述五列

典型边：

```text
source -> evidence
evidence -> claim
claim -> page
page -> page
claim -> claim
```

### `wiki_projection_state`

保存：

- canonical schema version；
- 最后完整重建时间；
- 最后处理事件；
- projection lag；
- projection status；
- 最后错误。

旧 `wiki_pages`、`wiki_links`、`wiki_ops` 在 v1.6.0 仍保留，仅提供兼容读取。

---

## 7. 核心服务设计

## 7.1 新增模块

### `src/models/wiki_v2.py`

定义：

- `WikiPage`
- `Claim`
- `Evidence`
- `ClaimRelation`
- `PageRegistryEntry`
- `SaveResult`
- `ValidationFinding`
- 枚举：PageType、PageStatus、ClaimStatus、EvidenceStance

要求：

- dataclass 或 Pydantic 模型二选一；
- 禁止在不同服务重复定义字典 schema；
- 提供严格 `from_dict/to_dict`；
- 对未知字段可配置 strict/compat 模式。

### `src/services/wiki_repository.py`

职责：

- canonical 页面与 Claim 的 CRUD；
- 稳定 ID；
- schema 校验；
- revision 乐观锁；
- staging 和原子替换；
- registry 更新；
- redirect；
- outbox 事件；
- transaction。

### `src/services/wiki_projection.py`

职责：

- canonical → SQLite v2；
- outbox 消费；
- 全量重建；
- parity 检查；
- FTS 建立；
- projection health。

### `src/services/wiki_claim_extractor.py`

输入：

- `knowledge_id`
- 原始 block 列表；
- source summary；
- 现有候选 Claims。

输出：

```python
ClaimExtractionResult(
    extracted_claims=[...],
    skipped_fragments=[...],
    llm_calls=...,
    warnings=[...],
)
```

要求：

- 先规则切句和候选筛选，再调用 LLM；
- LLM 必须输出结构化 JSON；
- 每条 Claim 必须携带 Evidence；
- 不允许只返回无来源事实；
- 有 block/location 时必须保留；
- LLM 调用失败不阻断 raw 索引。

### `src/services/wiki_claim_matcher.py`

职责：

- Claim 规范化；
- exact hash；
- lexical 候选；
- embedding 候选；
- 可选 reranker；
- 输出 merge action。

输出 action：

```python
ClaimMatchDecision(
    action="new|supports|refines|contradicts|supersedes|duplicate|unresolved",
    target_claim_id=None,
    score=0.0,
    reasons=[],
)
```

规则：

- exact normalized_statement 相同 → duplicate/supports；
- 语义相近但对象、数值或时间冲突 → contradicts；
- 新 Claim 时间更晚且明确替代旧 Claim → supersedes；
- 无足够置信度 → unresolved，不自动合并。

### `src/services/wiki_merge_engine.py`

职责：

- 将 extraction 与 match decision 应用到 Claim store；
- 更新 Evidence；
- 更新 Claim 状态；
- 更新 Page claim_ids；
- 生成人类可读 diff；
- 对冲突生成 review item。

### `src/services/wiki_dependency_service.py`

职责：

- 构建和查询依赖图；
- `get_impacted_by_source(knowledge_id)`；
- `get_impacted_by_claim(claim_id)`；
- 输出拓扑有序 rebuild plan；
- 防止环导致无限重编译。

### `src/services/wiki_rebuild_service.py`

职责：

- 来源更新和删除后的级联处理；
- staging 重编译；
- diff；
- publish/review；
- projection refresh。

### `src/services/wiki_feedback_service.py`

职责：

- 用户确认正确；
- 用户标记错误；
- 用户提供修正文案；
- 将反馈附着到 Claim；
- 调整状态，不直接修改 raw；
- 生成人工审阅事件。

### `src/services/wiki_validator.py`

检查：

- schema；
- page registry；
- claim 引用；
- evidence 完整性；
- source revision；
- status 合法性；
- redirect；
- projection parity；
- published 页面约束。

---

## 7.2 修改现有模块

### `src/services/knowledge_workflow.py`

当前流程：

```text
source compiler → entity updater → index → log
```

目标流程：

```text
source compiler
→ claim extractor
→ claim matcher
→ merge engine
→ page composer
→ repository transaction
→ index/log
→ projection outbox
```

要求：

- 保留失败隔离；
- raw 索引成功后，Wiki 编译失败不得破坏搜索；
- 返回结果增加：
  - `claims_created`
  - `claims_updated`
  - `conflicts`
  - `pages_updated`
  - `projection_pending`
  - `review_items`

### `src/services/wiki_entity_updater.py`

改造为：

- 不再直接写文件；
- 不负责最终 Claim ID；
- 只生成页面组织建议或页面摘要；
- 已有 `_write_entity_page` 标记 deprecated；
- v1.6 内部调用迁到 `WikiRepository`；
- 分类不再仅依赖“全大写短词”，至少增加：
  - LLM 输出 kind；
  - 规则 fallback；
  - 可配置类型词典。

### `src/services/wiki_write_service.py`

从“双写分发器”改为“统一 canonical 写入口”。

新行为：

```text
save answer
→ 查现有 Wiki
→ 提取 Claims
→ merge
→ 写 canonical
→ 写 outbox
→ 返回 projection_pending
```

兼容字段：

- 保留 `sqlite_page_id`，但标记 deprecated；
- 新增 `page_id`；
- `fs_saved` 替换为 `canonical_saved`；
- 旧调用方至少一个 minor 版本可继续读取旧字段。

### `src/services/wiki_compiler.py`

改为 compatibility adapter：

- `ingest()` 委托 `KnowledgeWorkflowService.compile()`；
- `save_answer()` 委托 `WikiWriteService.save()`；
- `_create_new_page`、`_update_existing_page` 不再成为主路径；
- 保留旧 API，输出 deprecation warning；
- 禁止新代码继续直接调用 `Database.insert_wiki_page`。

### `src/services/wiki_page_locator.py`

改造：

- 候选 ID 使用稳定 `page_id`；
- metadata 增加：
  - `revision`
  - `claim_ids`
  - `status`
  - `content_hash`
- 可优先从 projection 搜索；
- projection 不健康时 fallback 到文件扫描；
- fallback 行为进入 warnings。

### `src/services/wiki_fs_lint.py`

增强：

- schema_version；
- page_id 唯一性；
- registry parity；
- claim 文件存在性；
- Claim 至少一条有效 Evidence；
- Evidence 的 knowledge/block 是否存在；
- source revision 是否过期；
- published 页面不得引用 draft/unsupported Claim；
- claim anchor 与 claim_ids 一致；
- redirect 不得成环。

### `src/services/wiki_lint.py`

- 保持旧 SQLite lint 兼容；
- 新的统一入口根据 canonical v2 配置调用 `WikiValidator`；
- 输出 schema 保持 `LintReport` 兼容；
- 增加 category：
  - `schema_invalid`
  - `claim_missing`
  - `evidence_missing`
  - `evidence_stale`
  - `projection_drift`
  - `registry_drift`
  - `publish_gate_violation`

### `src/core/container.py`

新增 lazy properties：

- `wiki_repository`
- `wiki_projection`
- `wiki_claim_extractor`
- `wiki_claim_matcher`
- `wiki_merge_engine`
- `wiki_dependency_service`
- `wiki_rebuild_service`
- `wiki_feedback_service`
- `wiki_validator`

禁止服务内部随意 `new` 这些依赖，测试必须可注入 fake。

---

## 8. 写入与重编译流程

## 8.1 Ingest 流程

```text
1. parse file
2. index raw blocks / FTS / vectors
3. create or update source page
4. extract claims from blocks
5. retrieve candidate existing claims
6. classify merge actions
7. apply claim/evidence changes in staging
8. compose affected pages
9. validate staging
10. atomically publish canonical files
11. append log.md
12. enqueue projection events
13. refresh index.md
14. return compile report
```

### 失败规则

- 1–2 失败：按现有索引错误处理；
- 3–8 失败：raw 索引保留，Wiki compile report 标记 error；
- 9 失败：不得发布 staging；
- 10 失败：不得发送 projection event；
- 11–13 失败：canonical 已成功时记录 warning，可重试；
- 任何 LLM 失败不得留下半写页面。

---

## 8.2 来源更新流程

触发条件：

- 同一 `knowledge_id` 的 `content_hash` 变化；
- 文件 watcher 检测到修改；
- 手动 reindex；
- migration 导入新 revision。

处理：

```text
old source revision
→ new source revision
→ 找到旧 Evidence
→ 对相同 block 做 hash 比较
→ 未变化 Evidence 保留
→ 已变化 Evidence 标记 stale
→ 重新抽取新 Claims
→ 合并
→ 重新计算 Claim 状态
→ 找到受影响 Pages
→ staging 重写
→ validation
→ review/publish
```

不得把整个实体页视为必须全部重写。

---

## 8.3 来源删除流程

```text
knowledge deleted
→ 找到所有 Evidence
→ 将 Evidence 标记 removed
→ 重新计算 Claim
   ├─ 仍有 supports → active
   ├─ 仅剩冲突证据 → disputed
   └─ 无有效证据 → unsupported
→ 所有引用页面进入 review
→ 页面正文不得继续无标记展示 unsupported Claim
```

默认不物理删除 Claim，以保留审计历史。

---

## 8.4 Query Save 流程

```text
ask result
→ value gate
→ search existing pages
→ extract claims from answer + answer sources
→ match claims
→ decide:
   ├─ merge existing page
   ├─ create new synthesis/comparison
   └─ skip duplicate/low value
→ save draft
→ validation
→ projection event
```

### 自动保存门槛

沿用现有长度、confidence、source count 规则，并新增：

- 至少一个 Claim；
- 每个 Claim 至少一个 Evidence；
- 与现有页面的最大重复率低于阈值，或存在可合并增量；
- no-answer、warning 严重、来源不完整时禁止 auto save。

---

## 9. 配置设计

新增：

```yaml
knowledge_workflow:
  mode: wiki_first
  canonical_store: filesystem
  canonical_schema_version: 2
  wiki_dir: wiki
  staging_dir: wiki/_staging
  registry_path: wiki/_meta/pages.json
  redirects_path: wiki/_meta/redirects.json

wiki:
  canonical_v2:
    enabled: false
    projection_fallback_to_filesystem: true
    compatibility_read_legacy_sqlite: true
    compatibility_write_legacy_sqlite: false

  claims:
    enabled: true
    max_claims_per_ingest: 30
    max_llm_calls_per_ingest: 4
    exact_match_threshold: 1.0
    semantic_match_threshold: 0.88
    unresolved_threshold: 0.72
    require_block_evidence: true

  rebuild:
    auto_on_source_update: true
    auto_publish_low_risk: false
    max_pages_per_job: 100
    max_depth: 5

  projection:
    enabled: true
    auto_consume_outbox: true
    fallback_to_full_rebuild: true
    max_retry: 5

  validation:
    block_publish_on_error: true
    block_publish_on_warning: false
```

默认：

- 新项目可在验证完成后把 `canonical_v2.enabled` 设为 `true`；
- 老项目升级默认 `false`；
- migration 完成且 parity 通过后自动建议启用，不自动强制切换。

---

## 10. CLI 与 MCP 工具

## 10.1 CLI

新增：

```bash
shinehe wiki validate
shinehe wiki validate --strict
shinehe wiki migrate-v2 --dry-run
shinehe wiki migrate-v2 --apply
shinehe wiki sync-index
shinehe wiki rebuild --source <knowledge_id>
shinehe wiki rebuild --claim <claim_id>
shinehe wiki claims list
shinehe wiki claims show <claim_id>
shinehe wiki claims review <claim_id>
shinehe wiki projection-status
```

### `migrate-v2 --dry-run`

输出：

- A 轨页面数；
- B 轨页面数；
- 自动匹配数；
- 冲突数；
- 将创建的 canonical 页面；
- 将创建的 Claim；
- 无法溯源 facts；
- 预计 SQLite projection 行数；
- 备份路径；
- 是否满足 cutover 条件。

### `migrate-v2 --apply`

必须：

1. 获取全局 migration lock；
2. 备份 `data/`、`wiki/`；
3. 写入临时目录；
4. 完整 validation；
5. 原子替换 canonical；
6. 全量 projection；
7. parity 检查；
8. 成功后写配置；
9. 失败自动恢复备份。

---

## 10.2 MCP 工具

在 `admin` 或 `full` profile 增加：

- `wiki_get_page`
- `wiki_get_claim`
- `wiki_list_claims`
- `wiki_validate`
- `wiki_rebuild`
- `wiki_apply_feedback`
- `wiki_projection_status`
- `wiki_sync_projection`

`extended` profile 只增加只读能力：

- `wiki_get_page`
- `wiki_get_claim`
- `wiki_projection_status`

写操作继续受 `write_policy` 和 preview/confirm 约束。

---

## 11. 迁移方案

## 11.1 页面匹配顺序

迁移器按以下优先级将 A/B 轨页面匹配为同一 canonical 页面：

1. 已有显式 page ID；
2. 相同 source IDs 集合 + 规范化标题；
3. 相同标题 + 高内容相似度；
4. aliases 命中；
5. 无法确认则保留两个页面并生成 conflict report。

禁止仅凭同名直接合并内容差异明显的页面。

## 11.2 Claim 生成

迁移已有页面时：

- 将正文 `Facts` 列表拆为候选 Claim；
- 有 `source_ids` 但无 block 时，Evidence 只写 knowledge_id，并标记 `location_quality=page_only`；
- 后台 rebuild 可补 block 定位；
- 无来源 facts 标记 `draft` 或 `unsupported`；
- 不允许迁移后把无来源事实自动视为 active。

## 11.3 Cutover 条件

全部满足才可启用 `canonical_v2.enabled=true`：

- canonical validation 无 error；
- page registry parity = 100%；
- projection page parity = 100%；
- projection claim parity = 100%；
- 所有 published 页面引用的 Claim 合法；
- 迁移冲突已解决或明确保留；
- 核心 MCP 回归通过；
- retrieval eval 不低于基线；
- wiki eval 不低于基线。

## 11.4 回滚

保留：

```text
backups/wiki-v2-<timestamp>/
├── data/
├── wiki/
├── config.yaml
└── migration-report.json
```

回滚命令：

```bash
shinehe wiki migrate-v2 --rollback <timestamp>
```

回滚只恢复配置、wiki 与 projection；raw source 不做修改。

---

## 12. Agent 编码任务拆分

每个任务必须：

- 独立 commit；
- 先写失败测试；
- 不跨任务顺手大重构；
- 全量或相关回归通过后再进入下一任务；
- commit message 使用下方建议；
- 更新对应 spec 状态。

---

## Phase 0：基线与架构护栏

### T0.1 记录当前基线

执行：

```bash
pytest
ruff check src tests evals tools scripts
mypy src tools
python evals/run_retrieval_eval.py
python evals/run_wiki_eval.py
```

输出到：

```text
artifacts/eval/canonical-v2-baseline.json
```

内容包括测试数、Recall@5、MRR、nDCG、citation completeness、wiki 指标。

**验收：** baseline 文件可复现。

**commit：** `test(wiki-v2): record pre-migration quality baseline`

### T0.2 增加架构守卫测试

新增测试，禁止 canonical v2 开启后以下调用成为新主路径：

- `WikiCompiler` 直接 `Database.insert_wiki_page`
- `WikiEntityUpdater` 直接 `write_markdown`
- MCP handler 直接写页面
- API route 直接写页面

可采用 AST 测试或模块级依赖约束测试。

**验收：** 对现状先允许 allowlist，后续任务逐步清空。

**commit：** `test(wiki-v2): add canonical write boundary guards`

---

## Phase 1：数据模型与 Repository

### T1.1 新增 Wiki v2 模型

文件：

- `src/models/wiki_v2.py`
- `tests/test_wiki_v2_models.py`

测试：

- 合法模型 round-trip；
- 缺失必填字段失败；
- 非法状态失败；
- revision 非正整数失败；
- active Claim 无 supports Evidence 失败；
- published Page 引用 draft Claim 的跨对象校验由 validator 完成。

**commit：** `feat(wiki-v2): add canonical page claim evidence models`

### T1.2 实现 Schema Validator

文件：

- `src/services/wiki_validator.py`
- `schema/wiki-page-v2.schema.json`
- `schema/wiki-claim-v1.schema.json`
- `tests/test_wiki_validator.py`

**验收：** 能输出结构化 findings，包含 path、object_id、category、severity。

**commit：** `feat(wiki-v2): add executable canonical schemas and validator`

### T1.3 实现 WikiRepository

文件：

- `src/services/wiki_repository.py`
- `tests/test_wiki_repository.py`

测试必须覆盖：

- create page；
- update revision；
- stale expected_revision 冲突；
- atomic write；
- page rename 保持 page_id；
- registry 更新；
- Claim CRUD；
- transaction 中途失败不发布；
- outbox 事件顺序；
- Windows 路径兼容；
- 并发写冲突。

**commit：** `feat(wiki-v2): add filesystem canonical repository`

---

## Phase 2：SQLite Projection 与兼容读取

### T2.1 新增 Alembic migration

文件：

- 新 migration；
- `tests/test_wiki_v2_migration.py`

要求：

- 幂等；
- 新表存在；
- 旧表不删除；
- 空库和已有库均可升级；
- downgrade 至少能删除新表，不影响旧表。

**commit：** `feat(wiki-v2): add canonical projection schema`

### T2.2 实现 Projection Service

文件：

- `src/services/wiki_projection.py`
- `tests/test_wiki_projection.py`

测试：

- page/claim/evidence 投影；
- 事件重复消费；
- 中途失败重试；
- 全量 rebuild；
- parity；
- canonical 删除后 projection 清理；
- FTS 可搜索。

**commit：** `feat(wiki-v2): add idempotent sqlite projection`

### T2.3 Page Locator 切稳定 ID

修改：

- `src/services/wiki_page_locator.py`
- 相关 RAG 测试

**验收：**

- 候选 id 为 canonical `page_id`；
- projection 正常时使用 projection；
- projection 不可用时文件 fallback；
- SizeAwareRouter 行为不退化。

**commit：** `refactor(wiki-v2): resolve wiki candidates by stable page id`

---

## Phase 3：Claim 抽取与跨来源合并

### T3.1 Claim Extractor

文件：

- `src/services/wiki_claim_extractor.py`
- prompt/schema 文件；
- `tests/test_wiki_claim_extractor.py`

测试：

- 从多个 block 抽取；
- 每条 Claim 带 Evidence；
- location 保留；
- LLM 非 JSON；
- LLM 超时；
- 超过 Claim 上限；
- 重复句去重；
- 无可验证事实返回空。

**commit：** `feat(wiki-v2): extract evidence-backed claims from source blocks`

### T3.2 Claim Matcher

文件：

- `src/services/wiki_claim_matcher.py`
- `tests/test_wiki_claim_matcher.py`

测试 fixture：

- 完全相同；
- 同义支持；
- 数值冲突；
- 时间更新；
- 补充限定；
- 明确替代；
- 低置信 unresolved。

**commit：** `feat(wiki-v2): classify cross-source claim merge actions`

### T3.3 Merge Engine

文件：

- `src/services/wiki_merge_engine.py`
- `tests/test_wiki_merge_engine.py`

测试：

- supports 只增加 Evidence；
- duplicate 不新增 Claim；
- contradicts 将 Claim 标记 disputed；
- supersedes 建立 relation；
- transaction rollback；
- 页面 claim_ids 更新；
- diff 内容稳定。

**commit：** `feat(wiki-v2): merge claims without whole-page overwrite`

---

## Phase 4：主工作流切换与双轨收敛

### T4.1 重构 KnowledgeWorkflowService

修改：

- `src/services/knowledge_workflow.py`
- container；
- integration tests。

**验收：**

- ingest 产出 canonical Page/Claim；
- 失败不破坏 raw 检索；
- `index.md`、`log.md` 更新；
- projection pending 可见；
- 同一来源重复 ingest 幂等。

**commit：** `refactor(wiki-v2): compile ingest into canonical claims and pages`

### T4.2 改造 WikiWriteService

修改：

- `src/services/wiki_write_service.py`
- `src/services/rag_pipeline.py`
- MCP save handler。

**验收：**

- 不再双写两套独立内容；
- 旧返回字段兼容；
- 查询保存先 merge；
- 低价值或完全重复回答跳过；
- canonical 写成功、projection 失败时返回 warning 而非假失败。

**commit：** `refactor(wiki-v2): route query saves through canonical repository`

### T4.3 将 WikiCompiler 降级为适配器

修改：

- `src/services/wiki_compiler.py`
- deprecation tests。

**验收：**

- 旧 API 可用；
- 不直接写旧表；
- warning 可测试；
- 现有 MCP/GUI 不崩。

**commit：** `refactor(wiki-v2): convert legacy compiler to compatibility adapter`

---

## Phase 5：依赖图与自动失效传播

### T5.1 Dependency Service

文件：

- `src/services/wiki_dependency_service.py`
- tests。

测试：

- source→evidence→claim→page；
- 多来源；
- 环；
- 最大深度；
- 删除来源影响集；
- 拓扑排序稳定。

**commit：** `feat(wiki-v2): build source claim page dependency graph`

### T5.2 Rebuild Service

文件：

- `src/services/wiki_rebuild_service.py`
- job integration；
- tests。

测试：

- source update；
- source delete；
- unchanged block 不重编译；
- unsupported Claim；
- affected page review；
- staging validation；
- job cancel；
- 最大页面数保护。

**commit：** `feat(wiki-v2): propagate source changes through affected knowledge`

### T5.3 Watcher 与 Reindex 接入

修改：

- `path_indexer.py`
- `file_watcher.py`
- indexing job。

**验收：**

- 修改文件自动创建 rebuild job；
- 删除文件自动处理 Evidence；
- debounce 不重复创建大量 job；
- watcher 失败不阻断主进程。

**commit：** `feat(wiki-v2): trigger incremental knowledge rebuild from source changes`

---

## Phase 6：Migration、Lint、反馈与评测

### T6.1 迁移器

文件：

- `src/services/wiki_v2_migrator.py`
- CLI；
- fixtures；
- tests。

测试：

- A-only；
- B-only；
- A/B 可匹配；
- 同名冲突；
- 无来源事实；
- dry-run 零写入；
- apply；
- rollback；
- migration lock；
- Windows rename/replace。

**commit：** `feat(wiki-v2): migrate dual-track wiki into canonical store`

### T6.2 Validator/Lint 集成

修改：

- `wiki_fs_lint.py`
- `wiki_lint.py`
- CLI；
- MCP。

**验收：**

- 所有新增 finding category 可测；
- `shinehe wiki validate --strict` 非零退出码；
- 不破坏原 `LintReport` 字段；
- projection drift 可检测和修复。

**commit：** `feat(wiki-v2): validate claim provenance and projection parity`

### T6.3 用户反馈

文件：

- `src/services/wiki_feedback_service.py`
- MCP/API；
- tests。

行为：

- `confirm`；
- `reject`；
- `correct`；
- `needs_review`。

**验收：** 反馈形成 operation log 和 Claim 状态变化，不修改 raw。

**commit：** `feat(wiki-v2): apply user feedback to canonical claims`

### T6.4 知识演进评测

新增：

- `evals/run_knowledge_evolution_eval.py`
- fixtures。

指标：

| 指标 | 最低门槛 |
|---|---:|
| Claim Provenance Completeness | ≥ 0.95 |
| Evidence Location Completeness | ≥ 0.90 |
| Cross-source Merge Accuracy | ≥ 0.85 |
| Update Propagation Recall | = 1.00 |
| Unsupported Claim Detection | ≥ 0.95 |
| Page Identity Stability | = 1.00 |
| Migration Page Parity | = 1.00 |
| Projection Parity | = 1.00 |
| Retrieval Recall@5 Regression | 不低于基线 |
| No-answer Accuracy Regression | 不低于基线 |

**commit：** `test(wiki-v2): add knowledge evolution evaluation suite`

---

## 13. 测试策略

## 13.1 单元测试

覆盖：

- 模型；
- schema；
- repository；
- registry；
- claim extractor；
- matcher；
- merge engine；
- projection；
- dependencies；
- rebuild；
- migration；
- feedback。

## 13.2 集成测试

至少新增以下端到端场景：

### E2E-1：两来源支持同一事实

- ingest source A；
- 创建 Claim；
- ingest source B；
- 不创建重复 Claim；
- Evidence 数量从 1 变 2；
- 页面保持一个事实。

### E2E-2：新来源与旧来源冲突

- A 表述数值为 100；
- B 表述数值为 120；
- Claim 进入 disputed；
- 页面进入 review；
- lint 输出 contradiction。

### E2E-3：来源更新

- A v1 支持 Claim；
- A v2 删除该段；
- 旧 Evidence stale/removed；
- 若无其他来源，Claim unsupported；
- 引用页面进入 review。

### E2E-4：来源删除但仍有其他来源

- A、B 均支持 Claim；
- 删除 A；
- Claim 仍 active；
- Evidence 只剩 B；
- 页面无需降级为 unsupported。

### E2E-5：页面重命名

- 页面标题和文件路径改变；
- page_id 不变；
- MCP read 可用；
- redirect 生效；
- links 不丢失。

### E2E-6：Projection 故障

- canonical 写成功；
- SQLite 写失败；
- API 返回 projection pending；
- 重试后 parity 恢复；
- canonical 内容不丢失。

### E2E-7：查询学习

- 已有相关 synthesis；
- 新问答产生增量 Claim；
- 合并原页而不是新建重复页；
- 来源完整；
- draft review。

---

## 13.3 回归门禁

每个 Phase 结束必须执行：

```bash
pytest
ruff check src tests evals tools scripts
mypy src tools
python evals/run_retrieval_eval.py
python evals/run_wiki_eval.py
```

Phase 6 增加：

```bash
python evals/run_knowledge_evolution_eval.py
```

原则：

- 不允许以“既有失败”为由增加新失败；
- 基线债务必须在 Phase 0 记录；
- 新改动净增 0 lint/type 错误；
- retrieval 指标不得静默下降；
- Wiki v2 功能门控关闭时，legacy 行为必须零变化。

---

## 14. 安全与可靠性

### 14.1 文件安全

- 所有 canonical 写使用同目录临时文件 + `os.replace`；
- 多文件 transaction 在 `_staging/<tx_id>` 完成后再发布；
- 发布前 validator 必须通过；
- registry 最后替换；
- 崩溃恢复时扫描残留 staging，并提供 cleanup。

### 14.2 路径安全

- 所有页面路径必须在配置的 `wiki_dir` 内；
- 禁止 `..`；
- 禁止跟随越界符号链接；
- slug 与 page registry 双重校验；
- migration 导入时检查路径。

### 14.3 LLM 安全

- raw 内容视为不可信输入；
- prompt 明确忽略文档中的指令；
- 结构化 JSON schema 校验；
- LLM 不直接决定 published；
- Claim 无 Evidence 不得 active；
- 冲突低置信时进入 unresolved。

### 14.4 并发

- Repository 使用进程内锁 + 文件锁；
- expected_revision 防 lost update；
- projection 事件幂等；
- migrate 使用全局独占锁；
- watcher 与手动 rebuild 不得并发修改同一 page/claim。

---

## 15. 可观测性

新增 trace 字段：

- `wiki_tx_id`
- `page_id`
- `claim_id`
- `knowledge_id`
- `source_revision`
- `projection_event_id`
- `rebuild_job_id`
- `merge_action`
- `validation_error_count`

Health 增加：

```json
{
  "canonical_wiki": {
    "enabled": true,
    "schema_version": 2,
    "page_count": 123,
    "claim_count": 456,
    "validation_errors": 0
  },
  "projection": {
    "status": "healthy",
    "lag_events": 0,
    "last_rebuild_at": "..."
  }
}
```

不得记录完整敏感文档正文到日志。

---

## 16. 文档更新

完成后更新：

- `README.md`
- `README_zh.md`
- `PROGRESS.md`
- `docs/advanced-features.md`
- `docs/retrieval-quality.md`
- 新增 `docs/wiki/canonical-v2.md`
- 新增 `docs/migration/wiki-v2-migration.md`
- 更新 `schema/AGENTS.md`
- `src/version.py` 升至 `1.6.0`

README 主叙事调整为：

> Local-first canonical knowledge compiler and MCP retrieval engine.

而不是仅描述为检索引擎。

---

## 17. Definition of Done

本 Spec 完成必须同时满足：

1. 所有新 Wiki 写入只经过 `WikiRepository`；
2. Markdown canonical 是唯一权威内容；
3. SQLite v2 projection 可删除后完整重建；
4. 页面拥有稳定 page ID；
5. 每条 active Claim 至少有一条有效 Evidence；
6. Evidence 能定位到 block/location 时不得退化为仅 page/source；
7. 同一事实多来源不会重复创建多个 Claim；
8. 来源更新和删除可计算完整影响集；
9. unsupported/disputed Claim 能触发页面 review；
10. Query save 能合并已有页面；
11. migration dry-run、apply、rollback 全部可用；
12. legacy 模式行为不变；
13. 核心 MCP 契约不破坏；
14. 全量测试、ruff、mypy 全绿；
15. retrieval、wiki、knowledge evolution 三组评测达标；
16. 文档、配置、版本号一致；
17. 不删除旧表、不进行不可逆迁移；
18. 有完整 migration report 和 rollback 证据。

---

## 18. 编码 Agent 执行协议

将本 Spec 交给编码 Agent 时，附加以下约束：

```text
你正在 anjingdtl/knowledge-base 仓库中实现 Canonical Wiki v2。

执行规则：
1. 先读取本 Spec、CLAUDE.md、PROGRESS.md、当前 Wiki-first specs。
2. 先运行并记录现有测试与评测基线。
3. 严格按 Phase/T 编号执行，一次只做一个任务。
4. 每项任务先写失败测试，再写实现。
5. 每项任务独立 commit，不向 master 直接提交。
6. 不删除旧 wiki_pages/wiki_links/wiki_ops。
7. 不破坏 legacy 模式和现有核心 MCP 工具契约。
8. 发现 Spec 与真实代码冲突时：
   - 先核实调用链；
   - 在实施记录中写明偏差、风险与替代方案；
   - 选择最小破坏实现；
   - 不得静默偏离核心架构决策。
9. 禁止业务服务绕过 WikiRepository 直接写 canonical 文件。
10. 任一 Phase 未通过全量回归，不得进入下一 Phase。
11. 完成后更新 README、PROGRESS、迁移文档、配置示例和版本号。
12. 输出每个任务的：
   - 修改文件
   - 测试结果
   - 设计偏差
   - 剩余风险
   - commit SHA
```

---

## 19. 建议实施顺序与风险排序

| 顺序 | 阶段 | 风险 | 原因 |
|---:|---|---|---|
| 1 | Phase 0 | 低 | 只建立基线和护栏 |
| 2 | Phase 1 | 中 | 新模型与 Repository，不切主路径 |
| 3 | Phase 2 | 中 | 新 projection，与旧表并存 |
| 4 | Phase 3 | 中高 | Claim 语义合并是核心复杂点 |
| 5 | Phase 4 | 高 | 主写路径切换，需强回归 |
| 6 | Phase 5 | 高 | 来源变更传播影响范围大 |
| 7 | Phase 6 | 中高 | 迁移、回滚和真实评测 |

不得先做自动失效传播再补稳定 ID 和 Claim 模型，否则依赖图会建立在不稳定对象上。

---

## 20. 最终目标状态

完成后，ShineHeKnowledge 的产品定义应从：

> 带 Wiki 能力的本地 RAG/MCP 检索引擎

演进为：

> 以不可变原始资料为依据、以 Canonical Wiki 为持续知识层、具备 Claim 级证据链、跨来源合并、自动失效传播和 MCP 检索执行能力的本地知识编译系统。
