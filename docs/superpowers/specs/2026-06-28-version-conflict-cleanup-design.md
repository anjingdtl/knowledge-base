# Version-Conflict-Cleanup: 制度版本迭代冲突检测与清理设计

> 日期：2026-06-28
> 版本：v1.0
> 状态：Draft
> 场景：本地 agent 通过 MCP 调用的私人公司制度知识库

## 1. 背景与动机

### 1.1 场景痛点

ShineHe KB 的核心使用场景是公司制度知识库。制度类文档存在天然的**版本迭代**问题：

- 2022年劳动竞赛执行规章制度 与 2025年劳动竞赛执行规章制度 同时存在
- 旧版本已失效但未被清理，agent 查询时可能返回过时信息
- 全库规模预期 500-2000 条，需定期识别新旧版本对

### 1.2 现有能力盘点

| 能力 | 现状 | 评价 |
|------|------|------|
| 内容去重 | `scripts/dedup_cleanup.py` 仅比对 `content_hash` 完全相同 | ❌ 不适用版本迭代场景 |
| 级联删除 | `db.py#delete_knowledge` 已覆盖 chunks/versions/graph_nodes/graph_relations/entity_refs/blocks/block_refs | ✅ 底座完备 |
| 软删 + undo | `operation_log.py` 有完整 undo 机制 | ✅ 安全网就位 |
| 图谱关系 | `graph_builder.py` 有 `contradicts` 关系类型 | ⚠️ 未用于版本发现 |
| LLM 调用 | `LLMService` 已封装 | ✅ 现成 |
| Embedding | 三级缓存架构（L1 进程内 → L2 SQLite → L3 API）| ✅ 现成 |
| VectorStore | `search(query, top_k, min_score)` 接口完备 | ✅ 现成 |
| 异步任务 | `async_tasks.py` + `jobs` 路由的轮询模式 | ✅ 现成 |

### 1.3 不做什么

- ❌ 不做 cron 定时扫描（用户主动触发）
- ❌ 不动现有 `dedup_cleanup.py`（处理 content_hash 完全相同场景，边界清晰）
- ❌ 不新增 MCP 工具（纯前端维护操作，不暴露给 agent）
- ❌ 不新增 `effective_until` 等时效字段（用 LLM 语义判断更准）
- ❌ 不做"周期卷积"那套 Obsidian 借鉴（公司制度不是日记）

## 2. 核心设计

### 2.1 模块边界

```
src/
├── services/
│   └── version_conflict.py        # 核心服务：扫描+判断+清理编排
├── repositories/
│   └── conflict_repo.py           # 三张新表的 DAO
├── api/routes/
│   └── maintenance.py             # /maintenance/version-conflict 路由组
└── models/
    └── version_conflict.py        # 数据模型

client/src/views/
└── MaintenanceView.tsx            # 独立维护中心页
```

### 2.2 与现有模块的边界

- **不动** `dedup_cleanup.py`：它处理 content_hash 完全相同的场景，走脚本；新模块处理版本迭代，走 API
- **不动** `KnowledgeRepository.delete`/`purge_knowledge`：直接复用，新模块只调不实现
- **不动** `OperationLogService`：复用其 undo 能力作为安全网
- **不动** `graph_builder.py`：图谱关系由删除时的级联清理自动处理

### 2.3 数据流总览

```
用户点"开始扫描"
  → service.start_scan_session()
    → Phase1 SQL 粗筛（按 tag/分类/标题分词，过滤已忽略对）
    → Phase2 embedding 补充（对剩余文档跑 vectorstore.search，阈值 0.85+）
    → 候选对写入 conflict_pairs 表（状态 pending）
  → service.judge_pending_pairs(session_id)
    → 对每对候选调 LLM，写回 relation_type/confidence/reason
    → 用户在前端逐条查看
  → 用户操作：
    确认删除 → service.execute_delete(pair_id) → soft_delete + operation_log
    忽略    → service.ignore_pair(pair_id) → 写入 conflict_ignores
  → 回收站可恢复（走现有 soft_delete 体系）
```

## 3. 数据模型

### 3.1 新增三张表

#### 表 1：`conflict_sessions`（扫描会话表）

```sql
CREATE TABLE conflict_sessions (
    id TEXT PRIMARY KEY,                   -- UUID
    status TEXT NOT NULL DEFAULT 'scanning', -- 'scanning' | 'judging' | 'ready' | 'completed' | 'error'
    total_items_scanned INTEGER DEFAULT 0,
    candidates_found INTEGER DEFAULT 0,
    pairs_judged INTEGER DEFAULT 0,
    pairs_deleted INTEGER DEFAULT 0,
    pairs_ignored INTEGER DEFAULT 0,
    error TEXT,                            -- 扫描失败时的错误信息
    started_at TEXT NOT NULL,
    completed_at TEXT
);
```

#### 表 2：`conflict_pairs`（候选对表）

```sql
CREATE TABLE conflict_pairs (
    id TEXT PRIMARY KEY,                    -- UUID
    session_id TEXT NOT NULL,              -- 所属扫描会话
    item_a_id TEXT NOT NULL,               -- 知识条目 A
    item_b_id TEXT NOT NULL,               -- 知识条目 B
    candidate_source TEXT NOT NULL,        -- 'sql_tag' | 'sql_title' | 'embedding'
    similarity_score REAL,                 -- embedding 相似度（0-1），SQL 来源为 NULL
    -- LLM 判断结果（judge 阶段填充）
    relation_type TEXT,                    -- 'supersedes' | 'superseded_by' | 'partial_overlap' | 'unrelated'
    newer_item_id TEXT,                    -- 当 relation_type 为 supersedes/superseded_by 时指向新版
    confidence REAL,                       -- 0-1
    reason TEXT,                           -- LLM 给出的判断理由
    -- 状态机
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'confirmed' | 'ignored' | 'deleted'
    created_at TEXT NOT NULL,
    judged_at TEXT,
    resolved_at TEXT,                      -- 用户处理时间
    FOREIGN KEY (session_id) REFERENCES conflict_sessions(id)
);
CREATE INDEX idx_conflict_pairs_session ON conflict_pairs(session_id);
CREATE INDEX idx_conflict_pairs_status ON conflict_pairs(status);
CREATE INDEX idx_conflict_pairs_items ON conflict_pairs(item_a_id, item_b_id);
```

#### 表 3：`conflict_ignores`（忽略列表）

```sql
CREATE TABLE conflict_ignores (
    id TEXT PRIMARY KEY,
    item_a_id TEXT NOT NULL,
    item_b_id TEXT NOT NULL,
    -- 归一化存储：始终按 (min, max) 排序，避免 A/B 与 B/A 重复
    pair_key TEXT NOT NULL UNIQUE,          -- f"{min(item_a,item_b)}|{max(item_a,item_b)}"
    ignored_at TEXT NOT NULL,
    source_pair_id TEXT                    -- 关联来源 pair（可选追溯）
);
CREATE INDEX idx_conflict_ignores_pair ON conflict_ignores(pair_key);
```

### 3.2 状态机

```
conflict_sessions.status:
  scanning → judging → ready → completed
                ↓ (失败)
              error

conflict_pairs.status:
  pending → ignored   (用户判定误报，或 LLM 判断 unrelated)
         → deleted   (用户确认删除，执行 soft_delete 成功后)

注意：'ignored' 有两种来源，但行为不同：
- 用户主动忽略 → 同时写 conflict_ignores 表，下次扫描跳过
- LLM 判断 unrelated → 仅 pair.status='ignored'，不写 conflict_ignores，下次扫描仍会出现
```

### 3.3 关键设计点

- **pair_key 归一化**：忽略表用 `min(id_a, id_b)|max(id_a, id_b)` 作为唯一键，下次扫描时 SQL 粗筛阶段就能 JOIN 过滤
- **candidate_source 追溯**：记录每对候选是哪种粗筛路径发现的，便于调优阈值
- **judged_at 与 resolved_at 分离**：LLM 判断完是 judged_at，用户处理才是 resolved_at
- **session 可中断**：扫描中如果关页面，下次进来查 status=scanning 的会话可继续

## 4. 核心服务逻辑

### 4.1 `VersionConflictService` 接口

```python
class VersionConflictService:
    """版本冲突扫描与清理编排服务"""

    # 性能保护配置（适配 embedding 2000 RPM / 500K TPM，保守留余量）
    EMBEDDING_QPS = 3                    # 每秒 3 次 = 180 RPM（仅用 9% 配额）
    EMBEDDING_QUERY_MAX_TOKENS = 500     # 单次 query 截断
    LLM_BATCH_SIZE = 20                 # 每批判断对数
    LLM_QPS = 1                         # LLM 更保守
    MAX_CANDIDATES_PER_SESSION = 1000

    # ── 会话管理 ──
    def start_scan_session(self, rescan_ignored: bool = False) -> str:
        """创建新会话，返回 session_id。
        不阻塞，扫描在后台异步进行（复用现有 async_tasks 机制）。"""

    def get_session_status(self, session_id: str) -> dict:
        """查询会话进度：scanned/candidates/judged/deleted/ignored 计数。"""

    def list_sessions(self, status: str | None = None) -> list[dict]:
        """列出会话，用于前端选择"继续未完成的扫描"。"""

    # ── 扫描阶段 ──
    def _scan_phase_sql(self, session_id: str) -> list[dict]:
        """SQL 粗筛：按 tag/分类/标题分词分组，产出候选对。
        过滤掉 conflict_ignores 已存在的 pair_key。"""

    def _scan_phase_embedding(self, session_id: str, sql_pairs: set) -> list[dict]:
        """embedding 补充：对未被 SQL 命中的文档，跑 vectorstore.search
        找 top-N 相似文档（阈值 0.85+），产出补充候选对。
        复用现有 embedding 三级缓存（L1 进程内 → L2 SQLite → L3 API）。"""

    # ── 判断阶段 ──
    def judge_pending_pairs(self, session_id: str, limit: int = 20) -> dict:
        """对 session 内 status=pending 的候选对跑 LLM 判断。
        分批（默认20对/批），前端可轮询。
        返回 {judged: N, errors: [...]}"""

    # ── 用户操作 ──
    def list_pairs(self, session_id: str, status: str = "pending",
                   relation_type: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
        """分页查询候选对，JOIN knowledge_items 返回标题/创建时间等。"""

    def execute_delete(self, pair_id: str, operator: str = "user") -> dict:
        """确认删除旧版本。
        1. 读 pair 的 newer_item_id，确定要删的是另一侧
        2. 调 KnowledgeRepository.soft_delete_knowledge
        3. 写 operation_log（带快照，支持现有 undo）
        4. 更新 pair.status = 'deleted'
        返回 {deleted_id, log_id}"""

    def ignore_pair(self, pair_id: str) -> dict:
        """用户判定误报。
        1. 写 conflict_ignores（pair_key 归一化）
        2. 更新 pair.status = 'ignored'
        返回 {pair_id, ignored: True}"""
```

### 4.2 Phase 1 SQL 粗筛策略

两条路径并行：

**路径 1：按 tag 分组**

```sql
SELECT k1.id AS a, k2.id AS b
FROM knowledge_items k1
JOIN knowledge_items k2 ON k1.id < k2.id
WHERE k1.deleted_at IS NULL AND k2.deleted_at IS NULL
  AND k1.tags = k2.tags AND k1.tags != '[]'
  AND NOT EXISTS (
      SELECT 1 FROM conflict_ignores ci
      WHERE ci.pair_key = MIN(k1.id,k2.id)||'|'||MAX(k1.id,k2.id)
  )
```

**路径 2：按标题核心词分组**

去掉标题中的年份前缀（`\d{4}年`），剩余部分作为分组键。例如：
- "2022年劳动竞赛执行规章制度" → "劳动竞赛执行规章制度"
- "2025年劳动竞赛执行规章制度" → "劳动竞赛执行规章制度"

匹配后两两配对，同样过滤已忽略对。

### 4.3 Phase 2 embedding 补充策略

```python
def _scan_phase_embedding(self, session_id, sql_pairs):
    """对未被 SQL 命中的文档补充 embedding 相似度扫描"""
    # 已被 SQL 命中的 item_id 集合
    sql_item_ids = {a for pair in sql_pairs for a in pair}
    # 剩余未命中的文档
    remaining = self._knowledge_repo.list_active_ids() - sql_item_ids
    for item_id in remaining:
        item = self._knowledge_repo.get(item_id)
        # 复用现有 VectorStore.search（已有 embedding 三级缓存）
        similar = self._vectorstore.search(
            query=item["content"][:500],  # 截断到 500 字
            top_k=5,
            min_score=0.85
        )
        for hit in similar:
            if hit["id"] != item_id:
                pair_key = (min(item_id, hit["id"]), max(item_id, hit["id"]))
                if pair_key not in sql_pairs:
                    candidates.append({
                        "a": pair_key[0],
                        "b": pair_key[1],
                        "candidate_source": "embedding",
                        "similarity_score": hit["score"]
                    })
    return candidates
```

**限流**：受 `EMBEDDING_QPS = 3` 约束，每秒最多 3 次 vectorstore.search。

### 4.4 LLM 判断 Prompt

```python
JUDGE_PROMPT = """你是公司制度文档版本分析专家。判断以下两条知识是否为同一制度的不同版本。

## 知识条目 A
- ID: {id_a}
- 标题: {title_a}
- 创建时间: {created_a}
- 内容摘要: {content_a[:500]}

## 知识条目 B
- ID: {id_b}
- 标题: {title_b}
- 创建时间: {created_b}
- 内容摘要: {content_b[:500]}

## 判断要求
分析两条目关系，从以下四类中选一种：
- supersedes: A 是 B 的新版本（B 已被 A 替代）
- superseded_by: B 是 A 的新版本（A 已被 B 替代）
- partial_overlap: 部分内容重叠，但非整版本迭代
- unrelated: 无版本关系

## 输出格式（严格 JSON）
{{
  "relation_type": "supersedes|superseded_by|partial_overlap|unrelated",
  "newer_item_id": "A 或 B 的 ID（仅 supersedes/superseded_by 时填，否则空字符串）",
  "confidence": 0.0-1.0,
  "reason": "一句话说明判断依据（如'2025年版第3条将年假从5天增至7天，替代2022年版'）"
}}
"""
```

### 4.5 异步执行

复用现有 `async_tasks.py` + `jobs` 路由的轮询模式：

- `start_scan_session` 立即返回 session_id，扫描在后台 job 跑
- 前端轮询 `GET /sessions/{session_id}` 直到 status 变为 `ready`
- `judge_pending_pairs` 也走异步，分批判断避免阻塞

### 4.6 错误处理

- LLM 调用失败：该 pair 的 `relation_type` 留空，`confidence=0`，status 保持 pending，前端可手动重判
- soft_delete 失败：pair.status 不变，前端提示失败原因，operation_log 不写
- 整个会话失败：session.status → 'error'，error 字段记录堆栈

### 4.7 性能预估（2000 条库规模）

| 阶段 | 耗时 | 说明 |
|------|------|------|
| Phase 1 SQL 粗筛 | < 1s | 纯 SQL，无外部调用 |
| Phase 2 embedding 补充 | ≈ 11 分钟 | 2000 / 3 QPS，已 embed 文档命中缓存零消耗 |
| Phase 3 LLM 判断 | ≈ 33 分钟（最坏） | 1000 对 × 2s，异步分批用户边等边处理 |

## 5. API 路由设计

### 5.1 复用现有 jobs 体系

扫描和判断作为异步任务注册进 `ALLOWED_JOB_TYPES`：

```python
# src/api/routes/jobs.py 扩展
ALLOWED_JOB_TYPES = {
    "reindex_all",
    "wiki_compile",
    # ... 现有 ...
    "version_conflict_scan",   # 新增：扫描会话
    "version_conflict_judge",  # 新增：LLM 判断
}
```

### 5.2 维护中心专属路由

```python
maintenance_router = APIRouter(prefix="/maintenance", tags=["maintenance"],
                                dependencies=[Depends(_check_auth)])

# ── 会话管理 ──
POST   /maintenance/version-conflict/sessions
       # 创建扫描会话 → 触发 version_conflict_scan job
       # Body: {"rescan_ignored": false}
       # Returns: {"session_id": "...", "job_id": "..."}

GET    /maintenance/version-conflict/sessions
       # 列出会话（支持 status 过滤）
       # Query: ?status=scanning&limit=20&offset=0

GET    /maintenance/version-conflict/sessions/{session_id}
       # 会话详情（进度统计）

# ── 候选对查询 ──
GET    /maintenance/version-conflict/sessions/{session_id}/pairs
       # 分页查询候选对，JOIN knowledge_items 返回标题/创建时间
       # Query: ?status=pending&relation_type=supersedes&limit=50&offset=0

# ── 用户操作 ──
POST   /maintenance/version-conflict/pairs/{pair_id}/judge
       # 手动触发某对的 LLM 判断（单独重判）

POST   /maintenance/version-conflict/pairs/{pair_id}/delete
       # 确认删除旧版本
       # Body: {"operator": "user"}
       # Returns: {"deleted_item_id":..., "operation_log_id":...}

POST   /maintenance/version-conflict/pairs/{pair_id}/ignore
       # 忽略该对（写入 conflict_ignores）

# ── 忽略列表管理 ──
GET    /maintenance/version-conflict/ignores
       # 列出所有忽略记录（前端可查看/撤销）

DELETE /maintenance/version-conflict/ignores/{ignore_id}
       # 撤销忽略（下次扫描会重新判断）
```

### 5.3 job 与 session 的关系

```
用户调 POST /sessions
  → 创建 conflict_sessions 记录（status=scanning）
  → 创建 AsyncTaskService job（type=version_conflict_scan）
  → 返回 session_id + job_id

前端轮询两路：
  GET /jobs/{job_id}          → 看 job 执行状态（running/success/failed）
  GET /sessions/{session_id}  → 看扫描统计进度

扫描 job 完成后：
  → session.status → 'ready'
  → 前端自动调 GET /sessions/{id}/pairs?status=pending
```

## 6. 前端交互设计

### 6.1 `MaintenanceView.tsx` 页面结构

```
维护中心 (/maintenance)
├── 顶部：会话管理区
│   ├── "开始新扫描" 按钮（带确认弹窗：是否重新扫描已忽略项）
│   ├── 进行中会话进度条（轮询 GET /sessions/{id}，间隔 2s）
│   └── 历史会话列表（可点击查看旧会话结果）
│
├── 中部：候选对列表（核心交互区）
│   ├── 筛选器：status (pending/confirmed/ignored/deleted) + relation_type
│   ├── 分页表格，每行展示一对：
│   │   [A 标题 + 创建时间]  ←→  [B 标题 + 创建时间]
│   │   [relation_type 标签] [confidence 进度条] [LLM reason]
│   │   操作按钮：[查看详情] [确认删除旧版] [忽略] [重新判断]
│   └── 行内"查看详情"展开：两侧内容对照视图
│
└── 底部：忽略列表管理（可折叠）
    └── 表格：[A 标题] [B 标题] [忽略时间] [撤销忽略]
```

### 6.2 核心交互流程

```
1. 用户点"开始新扫描"
   → POST /sessions → 拿到 session_id
   → 页面切到"扫描进行中"状态，进度条每 2s 轮询 GET /sessions/{id}
   → status=scanning 时显示 "已扫描 X/Y，发现 Z 候选对"

2. 扫描完成（status=ready）
   → 自动加载 GET /sessions/{id}/pairs?status=pending
   → 表格展示候选对

3. 用户逐条处理：
   [确认删除] → 弹确认框显示"将删除 [旧版标题]，新版保留"
              → POST /pairs/{id}/delete
              → 行状态变 deleted，灰显
   [忽略]    → POST /pairs/{id}/ignore
              → 行状态变 ignored，灰显
   [重新判断] → POST /pairs/{id}/judge
              → 行 relation_type/confidence/reason 刷新

4. 扫描中断处理：
   用户关页面后回来 → 看到历史会话列表
   选中 status=scanning/judging 的会话 → 继续轮询
   pending 对仍可处理
```

### 6.3 内容对照视图

候选对行展开时，用简单的左右对照展示（不引入 diff 库）：

```
┌─────────────────┬─────────────────┐
│ A: 2022年劳动竞赛 │ B: 2025年劳动竞赛 │
├─────────────────┼─────────────────┤
│ 第三条：年假5天   │ 第三条：年假7天   │
│ 第四条：奖金上限1万│ 第四条：奖金上限2万│
│ ...              │ 第五条：新增...    │
└─────────────────┴─────────────────┘
```

展示内容截断到前 500 字，避免大文档渲染卡顿。

### 6.4 复用现有组件

- `DataTable.tsx`：候选对表格
- `Toast.tsx`：操作反馈
- `ErrorBoundary.tsx`：包裹维护页
- `useApi.ts`：API 调用封装
- `usePagination.ts`：候选对分页

### 6.5 入口位置

在 `Layout.tsx` 侧边栏加"维护中心"入口，位置在"知识库"下方。

## 7. 测试与安全

### 7.1 测试分层

| 层级 | 测试文件 | 覆盖重点 |
|------|----------|----------|
| 单元测试 | `tests/test_version_conflict.py` | `VersionConflictService` 各方法、状态机转换、pair_key 归一化 |
| 仓库测试 | `tests/test_conflict_repo.py` | `ConflictRepository` CRUD、忽略表唯一约束、JOIN 查询 |
| API 测试 | `tests/test_maintenance_api.py` | 路由鉴权、参数校验、错误响应、job 触发 |
| E2E 测试 | `tests/test_version_conflict_e2e.py` | 完整流程：扫描→判断→确认→软删→回收站恢复 |

### 7.2 关键测试用例

```python
# test_version_conflict.py
def test_scan_skips_ignored_pairs():
    """已忽略的 pair 不应再次出现在候选对中"""

def test_pair_key_normalization():
    """pair_key 应归一化为 min|max，A/B 与 B/A 等价"""

def test_execute_delete_targets_older_version():
    """确认删除时，应删 older 而非 newer（基于 newer_item_id）"""

def test_execute_delete_writes_operation_log():
    """删除应写 operation_log，支持 undo"""

def test_partial_overlap_not_auto_deleteable():
    """partial_overlap 不应自动删除，需用户手动判断"""

# test_conflict_repo.py
def test_ignore_pair_unique_constraint():
    """同 pair_key 二次插入应失败或 ON CONFLICT IGNORE"""

def test_list_pairs_joins_knowledge_items():
    """list_pairs 应 JOIN 返回标题，而非裸 id"""

# test_maintenance_api.py
def test_unauthenticated_request_rejected():
    """未认证请求应 401"""

def test_delete_nonexistent_pair_returns_404():
    """删除不存在的 pair 应 404"""

# test_version_conflict_e2e.py
def test_full_workflow_scan_judge_delete_restore():
    """扫描 → 判断 → 确认删除 → 回收站恢复 → 状态验证"""
```

### 7.3 测试夹具

复用 `tests/conftest.py` 现有 DB fixture，新增：
- `sample_versioned_policies`：构造 2022/2025 同名制度对
- `sample_unrelated_policies`：构造标题相似但内容无关的对（验证不误判）
- `sample_partial_overlap`：构造部分重叠的对

### 7.4 LLM mock

```python
@pytest.fixture
def mock_llm():
    """Mock LLMService，返回预设 JSON，避免真实 API 调用"""
    class FakeLLM:
        def chat(self, messages, silent=False):
            return '{"relation_type":"supersedes","newer_item_id":"B","confidence":0.9,"reason":"2025版替代2022版"}'
    return FakeLLM()
```

### 7.5 安全设计

#### 数据安全
- **删除路径**：强制走 `soft_delete`，不直接 `purge`；硬删只在回收站二次确认
- **快照**：`execute_delete` 前必写 `operation_log`（before 快照含全文），支持现有 undo
- **事务**：`execute_delete` 用 `db._write_lock` 保证原子性（删除条目 + 更新 pair.status）
- **cascade**：复用现有 `delete_knowledge(hard=False)`，chunks/graph/entity_refs 自动隔离

#### 防误删
- **partial_overlap 不允许直接删除**：前端禁用删除按钮，需用户手动选择删哪条
- **unrelated 仅置 pair.status='ignored'**：LLM 判断为 unrelated 的 pair 只更新 conflict_pairs.status='ignored'，**不写 conflict_ignores 表**（区别于用户主动忽略）。下次扫描如果再次命中，仍会重新判断
- **确认弹窗**：删除前必须显示"将删除 [旧版标题]，新版 [新版标题] 保留"，需二次点击

#### 鉴权
- 所有 `/maintenance` 路由复用 `_check_auth`
- 不新增权限分级（私人本地库，单用户场景）

#### 性能保护
```python
# 性能保护配置（适配 embedding 2000 RPM / 500K TPM，保守留余量）
EMBEDDING_QPS = 3                    # 每秒 3 次 = 180 RPM（仅用 9% 配额）
EMBEDDING_QUERY_MAX_TOKENS = 500     # 单次 query 截断
LLM_BATCH_SIZE = 20
LLM_QPS = 1                          # LLM 更保守
MAX_CANDIDATES_PER_SESSION = 1000
```

## 8. 实施清单

### 8.1 新增文件

| 文件 | 用途 |
|------|------|
| `alembic/versions/i001_version_conflict.py` | 三张新表的 migration |
| `src/models/version_conflict.py` | 数据模型 dataclass |
| `src/repositories/conflict_repo.py` | 三张表的 DAO |
| `src/services/version_conflict.py` | 核心编排服务 |
| `src/api/routes/maintenance.py` | REST 路由 |
| `client/src/views/MaintenanceView.tsx` | 前端维护页 |
| `tests/test_version_conflict.py` | 单元测试 |
| `tests/test_conflict_repo.py` | 仓库测试 |
| `tests/test_maintenance_api.py` | API 测试 |
| `tests/test_version_conflict_e2e.py` | E2E 测试 |

### 8.2 修改文件

| 文件 | 修改点 |
|------|--------|
| `src/api/routes/jobs.py` | `ALLOWED_JOB_TYPES` 加两个新类型 |
| `src/services/async_tasks.py` | 注册两个新 job handler |
| `src/app.py` 或路由注册处 | 挂载 `maintenance_router` |
| `client/src/components/Layout.tsx` | 侧边栏加"维护中心"入口 |
| `client/src/App.tsx` | 路由表加 `/maintenance` |

### 8.3 不动的文件

- `scripts/dedup_cleanup.py`（边界清晰，保留）
- `src/services/graph_builder.py`（不参与版本判断）
- `src/services/operation_log.py`（复用，不改）
- `src/repositories/knowledge_repo.py`（复用，不改）
- `src/services/embedding.py`（复用三级缓存，不改）
- `src/services/vectorstore.py`（复用 search 接口，不改）

## 9. 决策记录

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 库规模 | 500-2000 条 | 用户实际预期 |
| 粗筛策略 | SQL + embedding 混合 | 平衡准确度与调用量 |
| 判断粒度 | 四类关系 + 置信度 | 能处理部分重叠场景 |
| 删除策略 | 软删 → 回收站 → 可恢复 | 公司制度不可丢数据 |
| 误判处理 | 持久化忽略列表 | 避免重复打扰 |
| 扫描持久化 | 会话表 + 状态机 | 支持中断续传 |
| 架构方案 | 独立维护中心模块 | 职责单一，可扩展其他维护任务 |
| MCP 暴露 | 不暴露 | 纯前端维护操作，非 agent 查询路径 |
| 限流策略 | EMBEDDING_QPS=3, LLM_QPS=1 | 保守留余量，避免限流 |
