# Canonical Wiki V2 Phase 6：迁移、反馈与正式评测 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 A/B 轨 → Canonical Store 迁移（dry-run/apply/rollback）、Claim 层用户反馈、provenance/parity 校验增强，以及知识演进评测门禁；达到 Canonical Wiki V2 正式完成定义。

**Architecture:** 新增两个 DI 服务——`WikiV2Migrator`（扫描 A 轨 SQLite + B 轨 FS → 匹配 → 生成 Claim 候选 → 备份/锁/隔离写入/parity → 可 rollback）与 `WikiFeedbackService`（confirm/reject/correct/needs_review 仅作用于 Claim 状态 + operation log，不改 Raw Source）。校验增强挂在现有 `WikiValidator`/`WikiFsLint`；评测脚本 `evals/run_knowledge_evolution_eval.py` 复用 C2 黄金集 + Phase 5 rebuild 场景。

**Tech Stack:** Python 3 / SQLite / PyYAML / pytest / alembic / FastMCP。

**Spec / 契约:**
- `docs/superpowers/specs/2026-07-07-canonical-wiki-claim-provenance-design.md` §10–§11、§Phase6
- `docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md` §6 Phase 6
- `docs/architecture/wiki-v2-claim-merge-contract.md`（反馈不得绕过 merge 契约；不改 matcher 自动语义）

## Global Constraints

- **铁律**：Raw Source 是最终证据；无完整 supports evidence 不自动 active；Matcher 无法判断一律 unresolved（**不改** 5 个 xfail）；canonical 写入只经 `WikiRepository`；SQLite projection 只是可重建 read model；新服务构造函数 DI，禁 import `Config`/`Database`/`get_active_container`；`ALLOWED_DIRECT_WRITES` 保持空。
- **迁移铁律**：dry-run 零写入；apply 必须 lock + 备份 + 隔离生成 + validation + parity；**不得**在 apply 成功后自动强制 `canonical_v2.mode=primary`（只写 suggestion 到 report）；rollback 只恢复 config/wiki/projection，不改 raw。
- **反馈铁律**：反馈只改 Claim 状态/statement（correct 写 draft 修正），不改 Evidence 的 knowledge_id/block；不改 Raw Source；必须写 operation log。
- **C2 xfail**：5 个原样保留。
- **DI / 测试隔离**：per-test `wiki_dir` + 重置 container；`/wiki/` 不入版本控制。
- **风格**：4 空格；Python snake_case；commit 用 `feat(wiki-v2):` / `test(wiki-v2):` / `docs(wiki-v2):`。
- **每个 Task**：先失败测试 → 最小实现 → 绿 → ruff + mypy → 独立 commit。
- **Phase 完成**：全量 pytest + ruff + mypy + retrieval eval + wiki eval + knowledge evolution eval + PROGRESS/review。

---

## File Structure

| 文件 | 责任 | 创建/改动 |
|---|---|---|
| `src/services/wiki_v2_migrator.py` | dry-run / apply / rollback / lock / 页面匹配 / Claim 生成 | 新增 |
| `src/services/wiki_feedback_service.py` | confirm / reject / correct / needs_review | 新增 |
| `src/services/wiki_validator.py` | 增 provenance / projection parity findings | 改动 |
| `src/services/wiki_fs_lint.py` | 增 claim_provenance / unsupported / disputed 类 finding | 改动 |
| `src/core/container.py` | lazy property 注入 migrator + feedback | 改动 |
| `src/cli.py` | `wiki migrate-v2` / `wiki validate` / `wiki claims` | 改动 |
| `tests/test_wiki_v2_migrator.py` | 迁移器单测 | 新增 |
| `tests/test_wiki_feedback_service.py` | 反馈单测 | 新增 |
| `tests/test_canonical_write_guards.py` | C6 守卫覆盖新文件 | 改动 |
| `evals/run_knowledge_evolution_eval.py` | 10 项演进指标 | 新增 |
| `evals/wiki_v2/evolution_fixtures/` | 小 fixture（可选） | 新增 |
| `docs/migration/wiki-v2-migration.md` | 用户迁移手册 | 新增 |
| `PROGRESS.md` + phase6 review | 状态 | 改动 |
| `src/version.py` | → 1.6.0（Phase 6 全部完成时） | 改动 |

---

## Task T6.1a：WikiV2Migrator dry-run + 页面匹配

**Files:**
- Create: `src/services/wiki_v2_migrator.py`
- Create: `tests/test_wiki_v2_migrator.py`
- Modify: `tests/test_canonical_write_guards.py`（扫描新服务文件）

**Interfaces:**

```python
@dataclass
class MigrationPagePlan:
    track: str                    # "a" | "b" | "matched" | "conflict"
    source_ref: str               # a:page_id 或 b:relative_path
    title: str
    page_type: str
    source_ids: list[str]
    match_page_id: str | None     # 已有 canonical page_id 或将分配
    action: str                   # create | skip_already_canonical | conflict | match_a_b
    reasons: list[str] = field(default_factory=list)

@dataclass
class MigrationClaimPlan:
    statement: str
    source_ids: list[str]
    page_title: str
    status: str                   # draft | unsupported（无来源）
    location_quality: str         # page_only | missing
    action: str                   # create | skip

@dataclass
class MigrationReport:
    mode: str                     # dry_run | apply | rollback
    a_page_count: int = 0
    b_page_count: int = 0
    already_canonical: int = 0
    matched: int = 0
    conflicts: int = 0
    pages_to_create: int = 0
    claims_to_create: int = 0
    untraceable_facts: int = 0
    backup_path: str = ""
    lock_held: bool = False
    cutover_ready: bool = False
    suggestion: str = ""          # e.g. "consider primary after review"
    page_plans: list[MigrationPagePlan] = field(default_factory=list)
    claim_plans: list[MigrationClaimPlan] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    writes: int = 0               # dry-run 必须 0

class WikiV2Migrator:
    def __init__(
        self,
        wiki_dir: Path | str,
        repository: WikiRepository,
        *,
        database=None,              # 可选：读 A 轨 list_wiki_pages
        projection=None,            # 可选：apply 后 rebuild/parity
        backups_dir: Path | str | None = None,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[], str] | None = None,
        config: dict | None = None,
    ): ...

    def dry_run(self) -> MigrationReport: ...
    def apply(self) -> MigrationReport: ...
    def rollback(self, timestamp: str) -> MigrationReport: ...
```

**匹配优先级（spec §11.1）：**
1. 已有显式 `page_id`（B 轨已 canonical）→ `skip_already_canonical`
2. 相同 `source_ids` 集合 + 规范化标题 → match
3. 相同标题 + 内容相似度 ≥ 0.85（字符 bigram Jaccard）→ match
4. aliases 命中 → match
5. 同名但内容差异大 → `conflict`，**禁止合并**

**Claim 生成（spec §11.2）：**
- 解析 body 中 `## Facts` 下 `- ` 列表项
- 有 source_ids → Evidence(knowledge_id=sid, block_id=None)，`location_quality=page_only`，status=`draft`（**不**自动 active）
- 无来源 → status=`unsupported` 或 `draft`，计入 `untraceable_facts`
- 已有 claim_ids 且 claim 文件存在 → skip

- [ ] **Step 1: 写失败测试**

```python
# tests/test_wiki_v2_migrator.py
from pathlib import Path
from src.services.wiki_repository import WikiRepository
from src.services.wiki_v2_migrator import WikiV2Migrator


def _repo(tmp: Path) -> WikiRepository:
    wiki = tmp / "wiki"
    wiki.mkdir()
    return WikiRepository(
        wiki_dir=wiki,
        registry_path=wiki / "_meta" / "pages.json",
        redirects_path=wiki / "_meta" / "redirects.json",
        outbox_path=tmp / "outbox.jsonl",
    )


def _write_b_page(wiki: Path, rel: str, fm_title: str, body: str, *, page_id=None, source_ids=None, knowledge_id=None):
    path = wiki / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"title: {fm_title}", "page_type: entities"]
    if page_id:
        lines.append(f"page_id: {page_id}")
        lines.append("schema_version: 1")
        lines.append("status: published")
        lines.append("revision: 1")
        lines.append("claim_ids: []")
        lines.append("aliases: []")
        lines.append("tags: []")
        lines.append(f"source_ids: {source_ids or []}")
        lines.append("created_at: t")
        lines.append("updated_at: t")
        lines.append("content_hash: h")
    elif knowledge_id:
        lines.append(f"knowledge_id: {knowledge_id}")
    if source_ids and not page_id:
        lines.append(f"source_ids: {list(source_ids)}")
    lines += ["---", "", body]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_dry_run_zero_writes(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(repo._wiki_dir, "entities/alpha.md", "Alpha",
                  "## Facts\n- Alpha is a product\n", knowledge_id="k1")
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups")
    before = list(repo._wiki_dir.rglob("*"))
    report = m.dry_run()
    after = list(repo._wiki_dir.rglob("*"))
    assert report.mode == "dry_run"
    assert report.writes == 0
    assert report.b_page_count >= 1
    assert report.pages_to_create >= 1
    assert len(after) == len(before)


def test_dry_run_already_canonical_skipped(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(repo._wiki_dir, "entities/beta.md", "Beta", "body",
                  page_id="p-beta", source_ids=["k2"])
    # stage via repo so registry knows it
    from src.models.wiki_v2 import WikiPage, PageType, PageStatus
    page = WikiPage(schema_version=1, page_id="p-beta", title="Beta", page_type=PageType.ENTITIES,
                    status=PageStatus.PUBLISHED, revision=1, aliases=[], tags=[], source_ids=["k2"],
                    claim_ids=[], created_at="t", updated_at="t", content_hash="h", body="body")
    with repo.transaction() as tx:
        tx.stage_page(page)
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups")
    report = m.dry_run()
    assert report.already_canonical >= 1
    assert report.pages_to_create == 0


def test_dry_run_extracts_facts_as_draft_claims(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(repo._wiki_dir, "entities/gamma.md", "Gamma",
                  "## Facts\n- Gamma supports FTTR\n- Another fact\n", knowledge_id="k3")
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups")
    report = m.dry_run()
    assert report.claims_to_create == 2
    assert all(c.status == "draft" for c in report.claim_plans)
    assert all(c.location_quality == "page_only" for c in report.claim_plans)


def test_dry_run_untraceable_facts(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(repo._wiki_dir, "concepts/no-src.md", "NoSrc",
                  "## Facts\n- Orphan fact without source\n")
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups")
    report = m.dry_run()
    assert report.untraceable_facts >= 1
    assert any(c.status in ("draft", "unsupported") for c in report.claim_plans)


def test_same_title_different_content_is_conflict(tmp_path):
    repo = _repo(tmp_path)
    # 模拟 A 轨 page 通过 database fake
    class FakeDB:
        def list_wiki_pages(self, **kw):
            return [{
                "id": "a1", "title": "Dup", "content": "AAAA entirely different body text here",
                "source_ids": '["ka"]', "tags": "[]", "status": "active",
                "created_at": "t", "updated_at": "t",
            }]
    _write_b_page(repo._wiki_dir, "entities/dup.md", "Dup",
                  "BBBB completely other content for conflict", knowledge_id="kb")
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo, database=FakeDB(),
                       backups_dir=tmp_path / "backups")
    report = m.dry_run()
    assert report.conflicts >= 1
    assert any(p.action == "conflict" for p in report.page_plans)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_wiki_v2_migrator.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现 dry-run**

实现 `WikiV2Migrator`：扫 B 轨 `sources|entities|concepts|comparisons|syntheses/*.md`；可选扫 A 轨 `database.list_wiki_pages`；匹配；解析 Facts；组装 `MigrationReport`；**不写任何文件**。

辅助：
- `_normalize_title(t)` → lower strip 合并空白
- `_content_similarity(a, b)` → bigram Jaccard
- `_parse_facts(body)` → list[str]
- `_scan_b_pages()` / `_scan_a_pages()`

- [ ] **Step 4: 测试转绿 + ruff/mypy + commit**

```bash
pytest tests/test_wiki_v2_migrator.py -v
ruff check src/services/wiki_v2_migrator.py tests/test_wiki_v2_migrator.py
mypy src/services/wiki_v2_migrator.py
git add ... && git commit -m "feat(wiki-v2): dry-run dual-track page and claim migration plan"
```

---

## Task T6.1b：apply + lock + backup + rollback

**Files:**
- Modify: `src/services/wiki_v2_migrator.py`
- Modify: `tests/test_wiki_v2_migrator.py`
- Modify: `src/core/container.py`
- Modify: `src/cli.py`

**行为：**

`apply()`:
1. 获取全局 lock 文件 `backups_dir/.wiki_v2_migration.lock`（独占创建，已存在则失败）
2. dry_run 获取 plan
3. 备份 `wiki/`（及可选 config 快照）到 `backups/wiki-v2-<timestamp>/`
4. 对每个 `create` page/claim：经 `repository.transaction()` stage
5. Claim：无 active（一律 draft/unsupported）；Evidence page_only
6. 若有 projection：`process_outbox(force=True)` + 若有 `verify_parity` 则调用
7. 写 `migration-report.json` 到备份目录
8. `cutover_ready` 仅当 validation 无 error 且 conflicts==0；`suggestion` 提示人工切 primary，**不改 config**
9. 释放 lock（finally）

`rollback(timestamp)`:
1. 定位 `backups/wiki-v2-<timestamp>/`
2. 恢复 wiki 目录（先备份当前到 `.../pre-rollback/` 可选，或直接 replace）
3. 不改 raw；返回 report

- [ ] **Step 1: 测试**

```python
def test_apply_creates_canonical_pages_and_draft_claims(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(repo._wiki_dir, "entities/delta.md", "Delta",
                  "## Facts\n- Delta fact one\n", knowledge_id="k4")
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo,
                       backups_dir=tmp_path / "backups",
                       clock=lambda: "20260713T120000",
                       id_factory=lambda: "id-fixed-1")
    report = m.apply()
    assert report.mode == "apply"
    assert report.writes > 0
    assert report.backup_path
    assert (tmp_path / "backups" / "wiki-v2-20260713T120000").exists()
    pages = repo.list_pages()
    assert any(p.title == "Delta" for p in pages)
    claims = repo.list_claims()
    assert len(claims) >= 1
    assert all(c.status.value in ("draft", "unsupported") for c in claims)


def test_apply_lock_prevents_concurrent(tmp_path):
    repo = _repo(tmp_path)
    lock = tmp_path / "backups" / ".wiki_v2_migration.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("held", encoding="utf-8")
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups")
    report = m.apply()
    assert report.errors
    assert report.writes == 0


def test_rollback_restores_wiki(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(repo._wiki_dir, "entities/eps.md", "Eps",
                  "## Facts\n- Eps fact\n", knowledge_id="k5")
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo,
                       backups_dir=tmp_path / "backups",
                       clock=lambda: "20260713T130000")
    m.apply()
    # corrupt / change
    (repo._wiki_dir / "entities").mkdir(exist_ok=True)
    marker = repo._wiki_dir / "entities" / "marker-corrupt.md"
    marker.write_text("corrupt", encoding="utf-8")
    rb = m.rollback("20260713T130000")
    assert rb.mode == "rollback"
    assert not marker.exists() or "corrupt" not in marker.read_text(encoding="utf-8")


def test_apply_does_not_force_primary_mode(tmp_path):
    repo = _repo(tmp_path)
    cfg = {"wiki": {"canonical_v2": {"mode": "off"}}}
    m = WikiV2Migrator(wiki_dir=repo._wiki_dir, repository=repo,
                       backups_dir=tmp_path / "backups", config=cfg)
    _write_b_page(repo._wiki_dir, "entities/zeta.md", "Zeta", "body", knowledge_id="k6")
    report = m.apply()
    assert cfg["wiki"]["canonical_v2"]["mode"] == "off"
    assert "primary" in report.suggestion.lower() or report.suggestion
```

- [ ] **Step 2–4:** 实现、转绿、CLI `shinehe wiki migrate-v2 --dry-run|--apply|--rollback TS`、container 注入、commit

```text
feat(wiki-v2): migrate dual-track wiki into canonical store
```

---

## Task T6.2：Validator / Lint 集成

**Files:**
- Modify: `src/services/wiki_validator.py`
- Modify: `src/services/wiki_fs_lint.py`（可选轻量）
- Modify: `src/cli.py` → `shinehe wiki validate [--strict]`
- Create/Modify tests

**Findings 类别：**
- `missing_provenance`：active claim 无 supports evidence（已有 evidence_missing）
- `page_only_evidence`：warning，Evidence 无 block_id
- `projection_drift`：若注入 projection.verify_parity findings
- `unresolved_conflict`：page 引用 disputed claim（warning）

`validate --strict`：有 severity=error → exit 1

- [ ] 测试 + 实现 + commit `feat(wiki-v2): validate claim provenance and projection parity`

---

## Task T6.3：WikiFeedbackService

**Files:**
- Create: `src/services/wiki_feedback_service.py`
- Create: `tests/test_wiki_feedback_service.py`
- Modify: container + CLI（`wiki claims review`）

**API:**

```python
class FeedbackAction(str, Enum):
    CONFIRM = "confirm"           # draft/review → 若有 supports → active；否则保持
    REJECT = "reject"             # → retracted
    CORRECT = "correct"           # 提供新 statement → 新 revision draft + 原 claim 关系
    NEEDS_REVIEW = "needs_review" # → disputed 或保持 + 标记

@dataclass
class FeedbackResult:
    claim_id: str
    action: str
    before_status: str
    after_status: str
    op_log_id: str = ""
    errors: list[str] = field(default_factory=list)

class WikiFeedbackService:
    def __init__(self, repository, operation_log=None, clock=None): ...
    def apply(self, claim_id: str, action: str, *, correction: str | None = None,
              operator: str = "user", note: str = "") -> FeedbackResult: ...
```

**规则：**
- `confirm`：仅当 claim 有至少 1 条非 stale supports Evidence 才 → active；否则 error
- `reject` → retracted（revision+1）
- `correct`：要求 correction 非空；更新 statement + normalized；status=draft；revision+1
- `needs_review` → disputed
- 全部经 `repository.transaction().stage_claim`
- operation_log：`operation=wiki_feedback`, `target_type=claim`

- [ ] 测试 + 实现 + commit `feat(wiki-v2): apply user feedback to canonical claims`

---

## Task T6.4：知识演进评测

**Files:**
- Create: `evals/run_knowledge_evolution_eval.py`
- Create: `tests/test_knowledge_evolution_eval.py`（指标纯函数 + 小 fixture）

**10 项指标门槛（spec）：**

| 指标 | 门槛 |
|---|---:|
| Claim Provenance Completeness | ≥ 0.95 |
| Evidence Location Completeness | ≥ 0.90 |
| Cross-source Merge Accuracy | ≥ 0.85 |
| Update Propagation Recall | = 1.00 |
| Unsupported Claim Detection | ≥ 0.95 |
| Page Identity Stability | = 1.00 |
| Migration Page Parity | = 1.00 |
| Projection Parity | = 1.00 |
| Retrieval Recall@5 Regression | 不低于基线（可跳过若无 embedding） |
| No-answer Accuracy Regression | 不低于基线（可跳过） |

实现策略：
- Provenance / Location / Unsupported / Page Identity：对给定 wiki_dir + repo 扫描确定性计算
- Update Propagation：复用 Phase 5 rebuild 逻辑的小 fixture（u02/d02）
- Migration Parity：dry_run 后 apply 再扫 page 数
- Projection Parity：mock 或真实 verify_parity
- Retrieval：可选调用，失败标 skip 不 fail 总门禁（与 CI 无 embedding 对齐）

- [ ] 实现 + commit `test(wiki-v2): add knowledge evolution evaluation suite`

---

## Task T6.5：文档、PROGRESS、版本、全量门禁

- `docs/migration/wiki-v2-migration.md`
- `docs/superpowers/reviews/2026-07-13-phase6-review.md`
- 更新 `PROGRESS.md` 顶部
- `src/version.py` → `1.6.0`（仅当全部门禁绿）
- 全量：pytest / ruff / mypy / retrieval / wiki / knowledge_evolution

```text
docs(wiki-v2): complete phase6 migration feedback and evaluation
```

---

## 验收对照（纠偏方案 + Spec DoD）

| 条件 | Task |
|---|---|
| migration dry-run / apply / rollback 实测 | T6.1 |
| 反馈 → Claim 状态 + op log，不改 raw | T6.3 |
| 10 项演进指标脚本 | T6.4 |
| 不自动强制 primary | T6.1b |
| 守卫 allowlist 仍空；新服务进 C6 扫描 | T6.1 |
| 5 xfail 保留 | 全程 |
| 全量门禁不退化 | T6.5 |

---

## 风险

- A 轨页面 page_type 缺失 → 默认 `concepts`，warning
- 大库 apply 耗时 → 先 dry-run 报告规模
- Windows 文件锁 → lock 文件 + `os.replace`；测试覆盖
- feedback correct 是否应 supersede 原 claim → **本阶段仅改同一 claim 的 statement 为 draft**，不自动 supersede（保守，符合 merge 契约）
