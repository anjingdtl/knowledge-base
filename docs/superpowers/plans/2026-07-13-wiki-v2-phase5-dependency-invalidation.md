# Canonical Wiki V2 Phase 5：依赖图与失效传播 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 source(knowledge_id) 更新/删除后，按 block 哈希精准失效 Evidence、保守迁移 Claim/Page 状态、经 WikiRepository 事务级联重编译并刷新 projection。

**Architecture:** 三个新 DI 服务——`WikiDependencyService`（从 canonical 按需构建依赖图 + 影响规划）、`WikiRebuildService`（block-diff → stale 标记 → claim/page 迁移 → WikiTransaction → projection 刷新）、`RebuildScheduler`（per-kid debounce）。依赖图真源是 canonical 状态，`wiki_dependencies` 表为可重建 read model。所有 canonical 写入经 `WikiRepository.transaction()`。

**Tech Stack:** Python 3 / FastAPI / SQLite（sqlite-vec）/ PyYAML / pytest / alembic。

**Spec:** `docs/superpowers/specs/2026-07-13-wiki-v2-phase5-dependency-invalidation-design.md`

## Global Constraints

- **铁律**：Raw Source 是最终证据；无完整 supports evidence 不自动 active；Matcher 无法判断一律 unresolved（不改）；canonical 写入只经 `WikiRepository`；SQLite projection 只是可重建 read model；新服务构造函数 DI，禁 import `Config`/`Database`/`get_active_container`；canonical write guard allowlist 保持空、扫描范围不收缩；不删 legacy fallback、不扩自动发布、不抢跑 Phase 6。
- **C2 xfail**：5 个（单位/型号/地区/否定/强度词）原样保留，不绕过不删除。
- **DI container 隔离**：新测试必须重置 active container + per-test `wiki_dir`（复用 phase4c fixture 模式），防跨测试泄漏。
- **`/wiki/` 不入版本控制**：`.gitignore` 已含 `/wiki/`；测试 fixture 隔离 wiki_dir。
- **风格**：4 空格缩进；Python snake_case；中文注释允许；commit message 用 `feat(wiki-v2):` / `test(wiki-v2):` / `refactor(wiki-v2):` 风格。
- **每个 Task**：先写失败测试确认红灯 → 最小实现 → 跑绿 → ruff + mypy → 独立 commit。
- **每完成 Phase**：全量 pytest + ruff + mypy + retrieval eval + wiki eval + 更新 PROGRESS/review + commit。

---

## File Structure

| 文件 | 责任 | 创建/改动 |
|---|---|---|
| `src/models/wiki_v2.py` | `Evidence` 加 `stale`/`stale_at` | 改动 |
| `src/services/wiki_claim_extractor.py` | 抽出共享 `compute_excerpt_hash` | 改动 |
| `src/services/wiki_canary_workflow.py` / `wiki_primary_workflow.py` / `wiki_shadow_workflow.py` | `_hash_text` 改调 `compute_excerpt_hash` | 改动 |
| `src/services/wiki_dependency_service.py` | 依赖图 + 影响规划（`ImpactPlan` 等） | 新增 |
| `src/services/wiki_rebuild_service.py` | block-diff → stale → 迁移 → 事务 → projection | 新增 |
| `src/services/wiki_rebuild_scheduler.py` | per-kid debounce 合并 | 新增 |
| `src/services/wiki_projection.py` | 投影 `stale` 列 + `wiki_dependencies` 边 | 改动 |
| `src/services/db.py` | `wiki_claim_evidence` 加 `stale`/`stale_at` 列 | 改动 |
| `alembic/versions/j002_evidence_stale.py` | 老库补列（幂等） | 新增 |
| `src/core/container.py` | 3 个 lazy property | 改动 |
| `src/services/knowledge_workflow.py` | primary 门控触发 rebuild | 改动 |
| `src/services/path_indexer.py` / `src/services/file_watcher.py` | 文件变更 → scheduler | 改动 |
| `src/cli.py` | `shinehe rebuild` 命令 | 改动 |
| `tests/test_canonical_write_guards.py` | C6 守卫覆盖 3 新文件 | 改动 |
| `tests/test_wiki_dependency_service.py` | 依赖图测试 | 新增 |
| `tests/test_wiki_rebuild_service.py` | rebuild 测试 | 新增 |
| `tests/test_wiki_rebuild_scheduler.py` | scheduler 测试 | 新增 |
| `tests/test_wiki_v2_golden_eval.py` | 启用 source_update/source_delete | 改动 |
| `tests/test_wiki_v2_phase5_e2e.py` | E2E-3/E2E-4 集成测试 | 新增 |
| `config.example.yaml` | `rebuild.debounce_ms` + `auto_allowlist` | 改动 |
| `PROGRESS.md` + `docs/superpowers/reviews/2026-07-13-phase5-review.md` | 状态 + review | 改动 |

---

## Task T5.0：Evidence stale 字段 + 投影列 + alembic

**Files:**
- Modify: `src/models/wiki_v2.py`（`Evidence` dataclass）
- Modify: `src/services/db.py`（`wiki_claim_evidence` 建表 + `_SCHEMA`）
- Create: `alembic/versions/j002_evidence_stale.py`
- Test: `tests/test_wiki_v2_models.py`（已有，扩展）

**Interfaces:**
- Produces: `Evidence.stale: bool`、`Evidence.stale_at: str`（默认 `False`/`""`，`from_dict(strict=False)` 容忍旧文件无此字段）；投影列 `wiki_claim_evidence.stale INTEGER DEFAULT 0`、`stale_at TEXT DEFAULT ''`。

- [ ] **Step 1: 写失败测试（模型序列化）**

在 `tests/test_wiki_v2_models.py` 末尾追加：

```python
def test_evidence_stale_roundtrip():
    """Evidence.stale/stale_at 序列化往返。"""
    ev = Evidence(
        evidence_id="ev1", stance=EvidenceStance.SUPPORTS, knowledge_id="k1",
        block_id="b1", source_revision="v1", excerpt_hash="h1",
        stale=True, stale_at="2026-07-13T10:00:00",
    )
    d = ev.to_dict()
    assert d["stale"] is True
    assert d["stale_at"] == "2026-07-13T10:00:00"
    back = Evidence.from_dict(d)
    assert back.stale is True
    assert back.stale_at == "2026-07-13T10:00:00"


def test_evidence_stale_defaults_and_legacy_compat():
    """新 Evidence 默认 stale=False；旧 dict 无 stale 字段时 strict=False 兼容。"""
    ev = Evidence(evidence_id="ev2", stance=EvidenceStance.SUPPORTS, knowledge_id="k1")
    assert ev.stale is False
    assert ev.stale_at == ""
    legacy = {
        "evidence_id": "ev3", "stance": "supports", "knowledge_id": "k1",
    }
    back = Evidence.from_dict(legacy, strict=False)
    assert back.stale is False
    assert back.stale_at == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_wiki_v2_models.py::test_evidence_stale_roundtrip tests/test_wiki_v2_models.py::test_evidence_stale_defaults_and_legacy_compat -v`
Expected: FAIL（`Evidence` 无 `stale` 参数 / `to_dict` 无 `stale` 键）。

- [ ] **Step 3: 修改 `Evidence` dataclass**

`src/models/wiki_v2.py` 的 `Evidence`：

```python
@dataclass
class Evidence:
    evidence_id: str
    stance: EvidenceStance
    knowledge_id: str
    block_id: str | None = None
    location: dict = field(default_factory=dict)
    source_revision: str = ""
    excerpt_hash: str | None = None
    observed_at: str = ""
    stale: bool = False        # Phase 5:来源变更/删除后失效标记(保留可审计)
    stale_at: str = ""         # Phase 5:失效时间戳

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "Evidence":
        if not d.get("knowledge_id"):
            raise ValueError("Evidence 必须有 knowledge_id")
        stance = d["stance"] if isinstance(d["stance"], EvidenceStance) else EvidenceStance(d["stance"])
        return cls(
            evidence_id=d["evidence_id"], stance=stance, knowledge_id=d["knowledge_id"],
            block_id=d.get("block_id"), location=d.get("location") or {},
            source_revision=d.get("source_revision", ""), excerpt_hash=d.get("excerpt_hash"),
            observed_at=d.get("observed_at", ""),
            stale=bool(d.get("stale", False)), stale_at=d.get("stale_at", ""),
        )

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id, "stance": self.stance.value,
            "knowledge_id": self.knowledge_id, "block_id": self.block_id,
            "location": self.location, "source_revision": self.source_revision,
            "excerpt_hash": self.excerpt_hash, "observed_at": self.observed_at,
            "stale": self.stale, "stale_at": self.stale_at,
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_wiki_v2_models.py -v`
Expected: PASS（含新 2 例 + 已有模型测试不退化）。

- [ ] **Step 5: 写投影列失败测试**

`tests/test_wiki_projection.py` 追加（沿用该文件已有 fake DB fixture 风格；若无则参考 `tests/test_wiki_query_service.py` 的 `_FakeDB` 建 v2 表模式）：

```python
def test_projection_persists_evidence_stale(tmp_path):
    """projection._upsert_claim 把 evidence.stale 投影到 wiki_claim_evidence.stale 列。"""
    from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance
    from src.services.wiki_projection import WikiProjection
    db = _build_v2_db(tmp_path)  # 复用本文件已有 v2 建表 helper；含 stale/stale_at 列
    repo = _build_repo(tmp_path)  # 复用本文件已有 WikiRepository helper
    proj = WikiProjection(repository=repo, database=db, enabled=True)
    ev = Evidence(evidence_id="ev1", stance=EvidenceStance.SUPPORTS, knowledge_id="k1",
                  block_id="b1", stale=True, stale_at="2026-07-13T10:00:00")
    claim = _make_claim(claim_id="c1", evidence=[ev])  # 复用本文件 claim 工厂
    proj._upsert_claim(claim)
    row = db.get_conn().execute(
        "SELECT stale, stale_at FROM wiki_claim_evidence WHERE evidence_id = ?", ("ev1",)
    ).fetchone()
    assert row["stale"] == 1
    assert row["stale_at"] == "2026-07-13T10:00:00"
```

> 若 `tests/test_wiki_projection.py` 无现成 `_build_v2_db`/`_build_repo`/`_make_claim` helper，在本 task 内新增（参照 `tests/test_wiki_query_service.py:24-40` 的建表 SQL，补 `stale INTEGER DEFAULT 0, stale_at TEXT DEFAULT ''` 两列）。

- [ ] **Step 6: 跑测试确认失败**

Run: `pytest tests/test_wiki_projection.py::test_projection_persists_evidence_stale -v`
Expected: FAIL（投影未写 stale 列 / 列不存在）。

- [ ] **Step 7: 改 `db.py` 建表 + projection `_upsert_claim`**

`src/services/db.py` 的 `wiki_claim_evidence` 建表 SQL 加两列（在 `excerpt_hash`/`observed_at` 之后）：

```sql
stale INTEGER NOT NULL DEFAULT 0,
stale_at TEXT NOT NULL DEFAULT ''
```

`src/services/wiki_projection.py` 的 `_upsert_claim` evidence INSERT 加 `stale`/`stale_at`：

```python
conn.execute(
    "INSERT INTO wiki_claim_evidence "
    "(evidence_id, claim_id, stance, knowledge_id, block_id, "
    "location_json, source_revision, excerpt_hash, observed_at, stale, stale_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    (
        ev.evidence_id, claim.claim_id, ev.stance.value,
        ev.knowledge_id, ev.block_id,
        json.dumps(ev.location, ensure_ascii=False),
        ev.source_revision, ev.excerpt_hash, ev.observed_at,
        1 if ev.stale else 0, ev.stale_at,
    ),
)
```

同步更新 `tests/test_wiki_v2_transaction_recovery.py`、`tests/test_wiki_query_service.py`、`tests/test_wiki_canonical_mode.py` 中自建 `wiki_claim_evidence` 的 DDL，补 `stale INTEGER DEFAULT 0, stale_at TEXT DEFAULT ''`（否则 projection 测试因列缺失失败）。

- [ ] **Step 8: 跑测试确认通过**

Run: `pytest tests/test_wiki_projection.py tests/test_wiki_v2_models.py tests/test_wiki_v2_transaction_recovery.py tests/test_wiki_query_service.py tests/test_wiki_canonical_mode.py -q`
Expected: PASS。

- [ ] **Step 9: 创建 alembic j002 迁移**

`alembic/versions/j002_evidence_stale.py`：

```python
"""j002: add stale/stale_at to wiki_claim_evidence (Phase 5)

Revision ID: j002
Revises: j001
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa

revision = "j002"
down_revision = "j001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("wiki_claim_evidence")}
    if "stale" not in cols:
        op.add_column("wiki_claim_evidence",
                      sa.Column("stale", sa.Integer, nullable=False, server_default="0"))
    if "stale_at" not in cols:
        op.add_column("wiki_claim_evidence",
                      sa.Column("stale_at", sa.Text, nullable=False, server_default=""))


def downgrade() -> None:
    # 保守不删列:stale 是审计信息,downgrade 保留(对齐 d03)
    pass
```

- [ ] **Step 10: 跑迁移冒烟 + ruff + mypy**

Run: `pytest tests/test_wiki_v2_migration.py -q && ruff check src tests alembic && mypy src`
Expected: 迁移测试 PASS（j002 注册、wiki_dependencies 复合主键等已有测试不退化）、ruff 0、mypy 0。

- [ ] **Step 11: Commit**

```bash
git add src/models/wiki_v2.py src/services/db.py src/services/wiki_projection.py \
        alembic/versions/j002_evidence_stale.py \
        tests/test_wiki_v2_models.py tests/test_wiki_projection.py \
        tests/test_wiki_v2_transaction_recovery.py tests/test_wiki_query_service.py \
        tests/test_wiki_canonical_mode.py
git commit -m "feat(wiki-v2): add stale flag to evidence"
```

---

## Task T5.1：WikiDependencyService（依赖图 + 影响规划）

**Files:**
- Create: `src/services/wiki_dependency_service.py`
- Create: `tests/test_wiki_dependency_service.py`
- Modify: `src/core/container.py`（加 `wiki_dependency_service` property）
- Modify: `tests/test_canonical_write_guards.py`（C6 覆盖新文件）

**Interfaces:**
- Consumes: `repository.list_claims() -> list[Claim]`、`repository.list_pages() -> list[WikiPage]`（来自 T5.0 前已有）。
- Produces:
  - `ImpactPlan`、`EvidenceImpact`、`ClaimImpact`、`PageImpact`（dataclass，定义于本文件）
  - `WikiDependencyService(repository, config=None, *, clock=None)`
  - `get_impacted_by_source(knowledge_id: str, *, max_depth: int = 5) -> ImpactPlan`
  - `get_impacted_by_claim(claim_id: str, *, max_depth: int = 5) -> ImpactPlan`

- [ ] **Step 1: 写失败测试（E2E-4 场景：删 A 仍 active，剩 B）**

`tests/test_wiki_dependency_service.py`：

```python
"""WikiDependencyService 依赖图与影响规划测试。"""
from src.models.wiki_v2 import (
    Claim, ClaimStatus, ClaimRelation, Evidence, EvidenceStance, WikiPage, PageType, PageStatus,
)
from src.services.wiki_dependency_service import (
    ClaimImpact, EvidenceImpact, ImpactPlan, PageImpact, WikiDependencyService,
)


def _claim(cid, evidence, status=ClaimStatus.ACTIVE, relations=None):
    return Claim(
        schema_version=1, claim_id=cid, statement=f"stmt {cid}", normalized_statement=f"stmt {cid}",
        claim_type="fact", status=status, confidence=0.9, valid_from=None, valid_to=None,
        subject_refs=["s"], predicate="p", object_refs=["o"], evidence=evidence,
        relations=relations or [], created_at="t", updated_at="t", revision=1,
    )


def _ev(eid, kid, stance=EvidenceStance.SUPPORTS, stale=False):
    return Evidence(evidence_id=eid, stance=stance, knowledge_id=kid, block_id="b1",
                    source_revision="v1", excerpt_hash="h1", stale=stale)


def _page(pid, claim_ids, status=PageStatus.PUBLISHED):
    return WikiPage(
        schema_version=1, page_id=pid, title=f"Title {pid}", page_type=PageType.CONCEPTS,
        status=status, revision=1, aliases=[], tags=[], source_ids=[], claim_ids=claim_ids,
        created_at="t", updated_at="t", content_hash="ch", body="",
    )


class _FakeRepo:
    def __init__(self, claims, pages):
        self._claims = claims
        self._pages = pages
    def list_claims(self):
        return list(self._claims)
    def list_pages(self):
        return list(self._pages)


def test_get_impacted_by_source_multi_support_keeps_active():
    """E2E-4:A、B 均支持 c1 → 删 A(k1) 的影响集:c1 仍 active(剩 B 的 evidence)。"""
    c1 = _claim("c1", [_ev("eA", "k1"), _ev("eB", "k2")])  # A=k1, B=k2
    page1 = _page("p1", ["c1"])
    svc = WikiDependencyService(repository=_FakeRepo([c1], [page1]))
    plan = svc.get_impacted_by_source("k1")
    # eA 来自 k1 → 受影响 evidence;eB 来自 k2 → 不受影响
    assert {e.evidence_id for e in plan.affected_evidence} == {"eA"}
    # c1 仍有 eB(supports,非 stale)→ proposed_status 保持 active
    c1_impact = next(c for c in plan.affected_claims if c.claim_id == "c1")
    assert c1_impact.proposed_status == "active"


def test_get_impacted_by_source_single_support_becomes_unsupported():
    """E2E-3 片段:仅 A(k1) 支持 c1 → 删 A → c1 proposed unsupported。"""
    c1 = _claim("c1", [_ev("eA", "k1")])
    page1 = _page("p1", ["c1"])
    svc = WikiDependencyService(repository=_FakeRepo([c1], [page1]))
    plan = svc.get_impacted_by_source("k1")
    c1_impact = next(c for c in plan.affected_claims if c.claim_id == "c1")
    assert c1_impact.proposed_status == "unsupported"
    # 受影响 published page → proposed review
    p1_impact = next(p for p in plan.affected_pages if p.page_id == "p1")
    assert p1_impact.proposed_status == "review"


def test_topological_order_stable_and_cycle_detection():
    """claim↔claim 关系:c2 refines c1;拓扑序稳定(字典序);环 → cycle_warning + 截断。"""
    c1 = _claim("c1", [_ev("e1", "k1")])
    c2 = _claim("c2", [_ev("e2", "k1")], relations=[ClaimRelation("refines", "c1")])
    svc = WikiDependencyService(repository=_FakeRepo([c1, c2], []))
    plan = svc.get_impacted_by_claim("c2", max_depth=5)
    # c2 → c1(refines 边):两者都进 affected_claims
    assert {c.claim_id for c in plan.affected_claims} >= {"c1", "c2"}
    # 环:c1 refines c2 + c2 refines c1
    c1b = _claim("c1", [_ev("e1", "k1")], relations=[ClaimRelation("refines", "c2")])
    c2b = _claim("c2", [_ev("e2", "k1")], relations=[ClaimRelation("refines", "c1")])
    svc_cycle = WikiDependencyService(repository=_FakeRepo([c1b, c2b], []))
    plan_cycle = svc_cycle.get_impacted_by_claim("c1", max_depth=5)
    assert len(plan_cycle.cycle_warnings) >= 1


def test_max_depth_truncates_claim_relation_fanout():
    """claim 关系链超 max_depth → truncated=True。"""
    claims = []
    for i in range(7):
        rel = [ClaimRelation("refines", f"c{i-1}")] if i > 0 else []
        claims.append(_claim(f"c{i}", [_ev(f"e{i}", "k1")], relations=rel))
    svc = WikiDependencyService(repository=_FakeRepo(claims, []))
    plan = svc.get_impacted_by_claim("c6", max_depth=2)
    assert plan.truncated is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_wiki_dependency_service.py -v`
Expected: FAIL（模块不存在 / `ImportError`）。

- [ ] **Step 3: 实现 `WikiDependencyService`**

`src/services/wiki_dependency_service.py`：

```python
"""Canonical Wiki v2 依赖图与影响规划(Phase 5)。

依赖图真源 = canonical 状态:
    source(knowledge_id) --produces--> evidence --evidences--> claim
    claim --cited_in--> page
    claim --supersedes/refines/contradicts--> claim(环风险点)

影响规划从 repository 按需计算(遍历 list_claims/list_pages 构建邻接),
不依赖 wiki_dependencies 投影表。环检测 + max_depth(仅计 claim↔claim 传递)。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceImpact:
    evidence_id: str
    claim_id: str
    reason: str  # block_changed / block_deleted / source_deleted


@dataclass
class ClaimImpact:
    claim_id: str
    current_status: str
    proposed_status: str  # active→active | active→unsupported
    reason: str


@dataclass
class PageImpact:
    page_id: str
    current_status: str
    proposed_status: str  # published→review
    reason: str


@dataclass
class ImpactPlan:
    root: str
    affected_evidence: list[EvidenceImpact] = field(default_factory=list)
    affected_claims: list[ClaimImpact] = field(default_factory=list)
    affected_pages: list[PageImpact] = field(default_factory=list)
    topological_order: list[str] = field(default_factory=list)
    cycle_warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    stats: dict = field(default_factory=dict)


class WikiDependencyService:
    def __init__(self, repository: Any, config: Any = None, *,
                 clock: Callable[[], str] | None = None) -> None:
        self._repo = repository
        self._config = config
        self._clock = clock or (lambda: "")

    def get_impacted_by_source(self, knowledge_id: str, *, max_depth: int = 5) -> ImpactPlan:
        """source(knowledge_id) 变更/删除 → 受影响 evidence/claim/page + claim 关系传递。"""
        plan = ImpactPlan(root=knowledge_id)
        claims = self._repo.list_claims()
        pages = self._repo.list_pages()

        # 1. 该 source 的所有 evidence(非 stale)→ affected
        ev_to_claim: dict[str, str] = {}
        for claim in claims:
            for ev in claim.evidence:
                if ev.knowledge_id == knowledge_id and not ev.stale:
                    plan.affected_evidence.append(EvidenceImpact(
                        evidence_id=ev.evidence_id, claim_id=claim.claim_id,
                        reason="source_deleted"))
                    ev_to_claim[ev.evidence_id] = claim.claim_id

        # 2. 持有受影响 evidence 的 claim → 评估 proposed status
        touched_claim_ids: set[str] = {c for c in ev_to_claim.values()}
        self._evaluate_claims(claims, touched_claim_ids, knowledge_id, plan)

        # 3. claim↔claim 关系传递(环检测 + max_depth)
        self._fanout_claim_relations(claims, touched_claim_ids, max_depth, plan)

        # 4. 受影响 claim → page(published→review)
        self._evaluate_pages(pages, {c.claim_id for c in plan.affected_claims}, plan)

        plan.topological_order = self._topo_order(plan.affected_claims, claims)
        plan.stats = {
            "evidence": len(plan.affected_evidence),
            "claims": len(plan.affected_claims),
            "pages": len(plan.affected_pages),
            "cycles": len(plan.cycle_warnings),
        }
        return plan

    def get_impacted_by_claim(self, claim_id: str, *, max_depth: int = 5) -> ImpactPlan:
        """claim 变更 → 关联 page + claim↔claim 关系传递。"""
        plan = ImpactPlan(root=claim_id)
        claims = self._repo.list_claims()
        pages = self._repo.list_pages()
        seed = {claim_id}
        self._evaluate_claims(claims, seed, None, plan)
        self._fanout_claim_relations(claims, seed, max_depth, plan)
        self._evaluate_pages(pages, {c.claim_id for c in plan.affected_claims}, plan)
        plan.topological_order = self._topo_order(plan.affected_claims, claims)
        return plan

    # ---- 内部 ----

    def _evaluate_claims(self, claims, touched_ids, knowledge_id, plan):
        """评估受影响 claim 的 proposed_status:仍有他源 supports(active)否则 unsupported。"""
        by_id = {c.claim_id: c for c in claims}
        for cid in sorted(touched_ids):
            claim = by_id.get(cid)
            if claim is None:
                continue
            remaining = [
                e for e in claim.evidence
                if e.stance.value == "supports"
                and not e.stale
                and (knowledge_id is None or e.knowledge_id != knowledge_id)
            ]
            proposed = "active" if remaining else "unsupported"
            plan.affected_claims.append(ClaimImpact(
                claim_id=cid, current_status=claim.status.value,
                proposed_status=proposed,
                reason="remaining_supports" if remaining else "no_remaining_supports"))

    def _fanout_claim_relations(self, claims, seed, max_depth, plan):
        """BFS claim↔claim 关系传递;visited 防环;深度超 max_depth → truncated。"""
        by_id = {c.claim_id: c for c in claims}
        visited: set[str] = set()
        frontier = list(seed)
        depth = 0
        while frontier:
            if depth > max_depth:
                plan.truncated = True
                break
            nxt: list[str] = []
            for cid in frontier:
                if cid in visited:
                    continue
                # 环:cid 已在当前 BFS 路径(stack)→ warning
                visited.add(cid)
                claim = by_id.get(cid)
                if claim is None:
                    continue
                for rel in claim.relations:
                    tid = rel.target_claim_id
                    if tid in visited:
                        plan.cycle_warnings.append(
                            f"claim relation cycle at {cid} -> {tid} ({rel.relation})")
                        continue
                    nxt.append(tid)
                    if tid not in {c.claim_id for c in plan.affected_claims}:
                        self._evaluate_claims(claims, {tid}, None, plan)
            frontier = nxt
            depth += 1

    def _evaluate_pages(self, pages, claim_ids, plan):
        for page in pages:
            if page.status.value != "published":
                continue
            if set(page.claim_ids) & claim_ids:
                plan.affected_pages.append(PageImpact(
                    page_id=page.page_id, current_status=page.status.value,
                    proposed_status="review", reason="affected_claim"))

    def _topo_order(self, impacted, all_claims):
        """拓扑稳定:受影响 claim 按 claim_id 字典序(确定性,测试可复现)。"""
        return sorted({c.claim_id for c in impacted})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_wiki_dependency_service.py -v`
Expected: 4 例 PASS。

- [ ] **Step 5: container property**

`src/core/container.py`：在 `wiki_query_service` property 之后加：

```python
    @property
    def wiki_dependency_service(self):
        if self._wiki_dependency_service is None:
            from src.services.wiki_dependency_service import WikiDependencyService as _Dep
            self._wiki_dependency_service = _Dep(repository=self.wiki_repository, config=self.config)
            self._track_service("_wiki_dependency_service")
        return self._wiki_dependency_service
```

并在 `AppContainer` 的字段声明区（`_wiki_query_service` 附近）加：

```python
    _wiki_dependency_service: Optional[object] = field(default=None, repr=False)
```

- [ ] **Step 6: C6 守卫覆盖新文件**

`tests/test_canonical_write_guards.py` 的 `WIKI_V2_SERVICE_MODULES` 加 3 项（dependency 本 task，rebuild/scheduler 在后续 task 一并加；此处先加 dependency）：

```python
WIKI_V2_SERVICE_MODULES: set[str] = {
    "services/wiki_repository.py",
    "services/wiki_projection.py",
    "services/wiki_claim_extractor.py",
    "services/wiki_claim_matcher.py",
    "services/wiki_merge_engine.py",
    "services/wiki_page_locator.py",
    "services/wiki_query_service.py",
    "services/wiki_dependency_service.py",
}
```

- [ ] **Step 7: ruff + mypy + 守卫 + commit**

Run: `ruff check src tests && mypy src && pytest tests/test_canonical_write_guards.py -q`
Expected: 全 PASS（守卫确认新文件未 import 全局单例）。

```bash
git add src/services/wiki_dependency_service.py src/core/container.py \
        tests/test_wiki_dependency_service.py tests/test_canonical_write_guards.py
git commit -m "feat(wiki-v2): build source claim page dependency graph"
```

---

## Task T5.2a：WikiRebuildService.plan_rebuild（dry-run 影响规划）

**Files:**
- Create: `src/services/wiki_rebuild_service.py`
- Create: `tests/test_wiki_rebuild_service.py`
- Modify: `src/services/wiki_claim_extractor.py`（抽 `compute_excerpt_hash`）
- Modify: `src/services/wiki_canary_workflow.py` / `wiki_primary_workflow.py` / `wiki_shadow_workflow.py`（`_hash_text` 改调共享函数）

**Interfaces:**
- Consumes: `WikiDependencyService`（T5.1）、`BlockRepository.list_by_page(page_id, limit)`、`compute_excerpt_hash(text)`（本 task 抽出）。
- Produces:
  - `WikiRebuildService(repository, projection, block_repository, dependency_service, config=None, *, clock=None)`
  - `plan_rebuild(knowledge_id, *, event, max_depth=5, max_pages_per_job=100) -> ImpactPlan`，`event ∈ {"update","delete"}`；不写 canonical。

- [ ] **Step 1: 抽出共享 hash 函数（红灯）**

`tests/test_wiki_claim_extractor.py` 追加：

```python
def test_compute_excerpt_hash_stable():
    """compute_excerpt_hash 是 sha256 hex,稳定可复现。"""
    from src.services.wiki_claim_extractor import compute_excerpt_hash
    h = compute_excerpt_hash("hello world")
    assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    assert compute_excerpt_hash("") != compute_excerpt_hash("x")
```

- [ ] **Step 2: 跑确认失败 → 实现**

Run: `pytest tests/test_wiki_claim_extractor.py::test_compute_excerpt_hash_stable -v` → FAIL（ImportError）。

`src/services/wiki_claim_extractor.py` 顶部加：

```python
import hashlib


def compute_excerpt_hash(text: str) -> str:
    """块内容指纹(sha256 hex)。canary/primary/rebuild 共用,保证 block-diff 一致。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

把 `wiki_canary_workflow.py` / `wiki_primary_workflow.py` / `wiki_shadow_workflow.py` 各自的 `_hash_text` 方法体改为：

```python
    def _hash_text(self, text: str) -> str:
        from src.services.wiki_claim_extractor import compute_excerpt_hash
        return compute_excerpt_hash(text)
```

Run: `pytest tests/test_wiki_claim_extractor.py tests/test_wiki_canary_workflow.py tests/test_wiki_primary_workflow.py tests/test_wiki_shadow_workflow.py -q` → PASS（算法不变，行为等价）。

- [ ] **Step 3: 写 plan_rebuild 失败测试（u01-u03 / d01-d03 规划）**

`tests/test_wiki_rebuild_service.py`：

```python
"""WikiRebuildService 影响规划与 staged rebuild 测试。"""
import pytest

from src.models.wiki_v2 import (
    Claim, ClaimStatus, ClaimRelation, Evidence, EvidenceStance, WikiPage, PageType, PageStatus,
)
from src.services.wiki_dependency_service import WikiDependencyService
from src.services.wiki_rebuild_service import RebuildResult, WikiRebuildService


def _ev(eid, kid, block_id="b1", excerpt="h1", stance=EvidenceStance.SUPPORTS):
    return Evidence(evidence_id=eid, stance=stance, knowledge_id=kid, block_id=block_id,
                    source_revision="v1", excerpt_hash=excerpt)


def _claim(cid, evidence, status=ClaimStatus.ACTIVE):
    return Claim(schema_version=1, claim_id=cid, statement=cid, normalized_statement=cid,
                 claim_type="fact", status=status, confidence=0.9, valid_from=None, valid_to=None,
                 subject_refs=["s"], predicate="p", object_refs=["o"], evidence=evidence,
                 relations=[], created_at="t", updated_at="t", revision=1)


def _page(pid, claim_ids, status=PageStatus.PUBLISHED):
    return WikiPage(schema_version=1, page_id=pid, title=pid, page_type=PageType.CONCEPTS,
                    status=status, revision=1, aliases=[], tags=[], source_ids=[],
                    claim_ids=claim_ids, created_at="t", updated_at="t", content_hash="ch", body="")


class _FakeBlocks:
    """模拟 BlockRepository.list_by_page:返回 block_id→content_hash 映射对应的 block。"""
    def __init__(self, current_blocks: dict[str, str]):
        # current_blocks: {block_id: content}
        self._current = current_blocks
    def list_by_page(self, page_id, limit=1000):
        from src.models.block import Block
        from src.services.wiki_claim_extractor import compute_excerpt_hash
        return [
            Block(id=bid, page_id=page_id, content=content)
            for bid, content in self._current.items()
        ]


class _FakeRepo:
    def __init__(self, claims=None, pages=None):
        self._claims = claims or []
        self._pages = pages or []
        self.saved_claims = []
        self.saved_pages = []
    def list_claims(self):
        return list(self._claims)
    def list_pages(self):
        return list(self._pages)
    def get_claim(self, cid):
        return next((c for c in self._claims if c.claim_id == cid), None)


def _svc(repo, blocks, **kw):
    dep = WikiDependencyService(repository=repo)
    return WikiRebuildService(repository=repo, projection=_NoopProjection(),
                              block_repository=blocks, dependency_service=dep,
                              config={"wiki.rebuild.max_pages_per_job": 100, "wiki.rebuild.max_depth": 5},
                              clock=lambda: "NOW", **kw)


class _NoopProjection:
    enabled = True
    def process_outbox(self, *, force=False):
        return type("R", (), {"processed": 0, "skipped": 0, "warnings": [], "errors": []})()
    def verify_parity(self):
        return []


# ---- u02:来源更新且 block 变 → 变化 evidence 标 stale ----
def test_plan_update_changed_block_marks_stale():
    c1 = _claim("c1", [_ev("e1", "k1", block_id="b1", excerpt="OLD")])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    # 当前 b1 内容变了 → excerpt_hash 不同
    blocks = _FakeBlocks({"b1": "NEW CONTENT"})
    svc = _svc(repo, blocks)
    plan = svc.plan_rebuild("k1", event="update")
    ev_impact = next(e for e in plan.affected_evidence if e.evidence_id == "e1")
    assert ev_impact.reason == "block_changed"
    c1_impact = next(c for c in plan.affected_claims if c.claim_id == "c1")
    assert c1_impact.proposed_status == "unsupported"


# ---- u01/u03:来源更新但 block 未变 → 不失效,不重编译 ----
def test_plan_update_unchanged_block_no_impact():
    from src.services.wiki_claim_extractor import compute_excerpt_hash
    h = compute_excerpt_hash("SAME")
    c1 = _claim("c1", [_ev("e1", "k1", block_id="b1", excerpt=h)])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    blocks = _FakeBlocks({"b1": "SAME"})  # 内容不变 → hash 同
    svc = _svc(repo, blocks)
    plan = svc.plan_rebuild("k1", event="update")
    assert plan.affected_evidence == []
    assert plan.affected_claims == []


# ---- u02 变体:block 被删 → block_deleted ----
def test_plan_update_block_deleted():
    c1 = _claim("c1", [_ev("e1", "k1", block_id="bGone", excerpt="h")])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    blocks = _FakeBlocks({})  # bGone 不在当前 blocks
    svc = _svc(repo, blocks)
    plan = svc.plan_rebuild("k1", event="update")
    assert plan.affected_evidence[0].reason == "block_deleted"


# ---- d02:删来源且无他源 → unsupported;d01:有他源 → active ----
def test_plan_delete_no_other_supports_unsupported():
    c1 = _claim("c1", [_ev("e1", "k1")])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    svc = _svc(repo, _FakeBlocks({}))
    plan = svc.plan_rebuild("k1", event="delete")
    assert plan.affected_evidence[0].reason == "source_deleted"
    assert next(c for c in plan.affected_claims if c.claim_id == "c1").proposed_status == "unsupported"


def test_plan_delete_with_other_supports_active():
    c1 = _claim("c1", [_ev("e1", "k1"), _ev("e2", "k2")])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    svc = _svc(repo, _FakeBlocks({}))
    plan = svc.plan_rebuild("k1", event="delete")
    assert next(c for c in plan.affected_claims if c.claim_id == "c1").proposed_status == "active"
```

- [ ] **Step 4: 跑确认失败**

Run: `pytest tests/test_wiki_rebuild_service.py -v` → FAIL（`wiki_rebuild_service` 不存在）。

- [ ] **Step 5: 实现 `WikiRebuildService.plan_rebuild`**

`src/services/wiki_rebuild_service.py`：

```python
"""Canonical Wiki v2 来源失效传播 staged rebuild(Phase 5)。

source 更新/删除 → block 哈希比对 → evidence stale → claim/page 状态迁移 →
WikiRepository 事务落盘 → projection 刷新。保守:published→review,unsupported 不 retract。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.models.wiki_v2 import Claim, ClaimStatus, EvidenceStance, PageStatus, WikiPage
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_dependency_service import (
    ClaimImpact, EvidenceImpact, ImpactPlan, WikiDependencyService,
)


@dataclass
class RebuildResult:
    knowledge_id: str
    event: str
    plan: ImpactPlan
    committed: bool = False
    cancelled: bool = False
    warnings: list[str] = field(default_factory=list)


class WikiRebuildService:
    def __init__(self, repository: Any, projection: Any, block_repository: Any,
                 dependency_service: WikiDependencyService, config: Any = None, *,
                 clock: Callable[[], str] | None = None) -> None:
        self._repo = repository
        self._projection = projection
        self._blocks = block_repository
        self._dep = dependency_service
        self._config = config
        self._clock = clock or (lambda: "")

    def _cfg(self, key: str, default: Any = None) -> Any:
        if self._config is not None:
            return self._config.get(key, default)
        return default

    def plan_rebuild(self, knowledge_id: str, *, event: str,
                     max_depth: int | None = None, max_pages_per_job: int | None = None) -> ImpactPlan:
        """规划受影响集(dry-run 语义:不写)。event ∈ {"update","delete"}。"""
        md = max_depth if max_depth is not None else int(self._cfg("wiki.rebuild.max_depth", 5))
        mpp = max_pages_per_job if max_pages_per_job is not None else int(self._cfg("wiki.rebuild.max_pages_per_job", 100))
        plan = self._dep.get_impacted_by_source(knowledge_id, max_depth=md)
        plan.stats["event"] = event

        if event == "delete":
            # 删除:该 source 全部 evidence → source_deleted(reason 已由 dep 标)
            return self._cap_pages(plan, mpp)

        # update:block 哈希比对,精修 evidence reason
        current_hashes = self._current_block_hashes(knowledge_id)
        refined: list[EvidenceImpact] = []
        claims_by_id = {c.claim_id: c for c in self._repo.list_claims()}
        for ev_impact in plan.affected_evidence:
            claim = claims_by_id.get(ev_impact.claim_id)
            ev = next((e for e in claim.evidence if e.evidence_id == ev_impact.evidence_id), None) if claim else None
            if ev is None or not ev.block_id:
                refined.append(ev_impact)  # 无 block_id 证据:保守按失效
                continue
            if ev.block_id not in current_hashes:
                refined.append(EvidenceImpact(ev_impact.evidence_id, ev_impact.claim_id, "block_deleted"))
            elif current_hashes[ev.block_id] != ev.excerpt_hash:
                refined.append(EvidenceImpact(ev_impact.evidence_id, ev_impact.claim_id, "block_changed"))
            else:
                continue  # 未变化 → 不失效(u01/u03)
        plan.affected_evidence = refined
        # 重估 claim proposed_status:只看真正失效的 evidence
        self._reevaluate_claims(plan, knowledge_id)
        return self._cap_pages(plan, mpp)

    def _current_block_hashes(self, knowledge_id: str) -> dict[str, str]:
        rows = self._blocks.list_by_page(knowledge_id, limit=10000)
        out: dict[str, str] = {}
        for blk in rows:
            bid = getattr(blk, "id", None) or getattr(blk, "block_id", None)
            content = getattr(blk, "content", "")
            if bid and content:
                out[str(bid)] = compute_excerpt_hash(content)
        return out

    def _reevaluate_claims(self, plan: ImpactPlan, knowledge_id: str) -> None:
        """block-diff 后重估:失效 evidence 集改变 → claim proposed_status 重算。"""
        stale_ev_by_claim: dict[str, set[str]] = {}
        for ev in plan.affected_evidence:
            stale_ev_by_claim.setdefault(ev.claim_id, set()).add(ev.evidence_id)
        claims_by_id = {c.claim_id: c for c in self._repo.list_claims()}
        new_impacts: list[ClaimImpact] = []
        impacted_ids = {c.claim_id for c in plan.affected_claims} | set(stale_ev_by_claim)
        for cid in sorted(impacted_ids):
            claim = claims_by_id.get(cid)
            if claim is None:
                continue
            stale_set = stale_ev_by_claim.get(cid, set())
            remaining = [
                e for e in claim.evidence
                if e.stance is EvidenceStance.SUPPORTS and not e.stale
                and e.evidence_id not in stale_set
                and e.knowledge_id != knowledge_id  # 他源
            ]
            # 同源但 block 未变的 supports 也算 remaining(update 场景)
            remaining += [
                e for e in claim.evidence
                if e.stance is EvidenceStance.SUPPORTS and not e.stale
                and e.evidence_id not in stale_set
                and e.knowledge_id == knowledge_id
            ]
            proposed = "active" if remaining else "unsupported"
            new_impacts.append(ClaimImpact(
                claim_id=cid, current_status=claim.status.value,
                proposed_status=proposed,
                reason="remaining_supports" if remaining else "no_remaining_supports"))
        plan.affected_claims = new_impacts

    def _cap_pages(self, plan: ImpactPlan, max_pages: int) -> ImpactPlan:
        if len(plan.affected_pages) > max_pages:
            plan.stats["pending_pages"] = [p.page_id for p in plan.affected_pages[max_pages:]]
            plan.affected_pages = plan.affected_pages[:max_pages]
            plan.truncated = True
        return plan
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_wiki_rebuild_service.py -v` → 5 例 PASS。

- [ ] **Step 7: ruff + mypy + commit**

Run: `ruff check src tests && mypy src` → 0 error。

```bash
git add src/services/wiki_rebuild_service.py src/services/wiki_claim_extractor.py \
        src/services/wiki_canary_workflow.py src/services/wiki_primary_workflow.py \
        src/services/wiki_shadow_workflow.py \
        tests/test_wiki_rebuild_service.py tests/test_wiki_claim_extractor.py
git commit -m "feat(wiki-v2): plan source rebuild impact"
```

---

## Task T5.2b：WikiRebuildService.rebuild（staging 事务 + projection + cancel）

**Files:**
- Modify: `src/services/wiki_rebuild_service.py`（加 `rebuild` + `RebuildJob`）
- Modify: `tests/test_wiki_rebuild_service.py`（加 rebuild 测试）

**Interfaces:**
- Consumes: `repository.transaction()` 上下文管理器（`stage_claim` / `stage_page` / `commit`）、`projection.process_outbox()` / `verify_parity()`。
- Produces:
  - `RebuildJob`（持 `cancel_event`，`cancel()` 方法）
  - `WikiRebuildService.rebuild(knowledge_id, *, event, job=None, dry_run=False) -> RebuildResult`

- [ ] **Step 1: 写 rebuild 失败测试（E2E-3 + cancel + max_pages）**

追加到 `tests/test_wiki_rebuild_service.py`：

```python
import threading
from src.services.wiki_rebuild_service import RebuildJob


def test_rebuild_update_stages_stale_and_unsupported(tmp_path):
    """E2E-3:update 删段 → evidence stale + claim unsupported + page review,经事务落盘。"""
    from src.services.wiki_repository import WikiRepository
    c1 = _claim("c1", [_ev("e1", "k1", block_id="b1", excerpt="OLD")])
    c1.revision = 1
    repo = _RecordingRepo([c1], [_page("p1", ["c1"])])
    blocks = _FakeBlocks({"b1": "NEW"})
    svc = _svc(repo, blocks)
    result = svc.rebuild("k1", event="update")
    assert result.committed is True
    # claim 被暂存:status unsupported,evidence stale
    staged_claim = repo.staged_claims["c1"]
    assert staged_claim.status is ClaimStatus.UNSUPPORTED
    assert staged_claim.evidence[0].stale is True
    assert staged_claim.evidence[0].stale_at == "NOW"
    # page 被暂存:review
    assert repo.staged_pages["p1"].status is PageStatus.REVIEW


def test_rebuild_delete_keeps_claim_no_physical_delete(tmp_path):
    """d03:删来源 → claim unsupported 但不物理删除(仍存在于 list_claims,审计保留)。"""
    c1 = _claim("c1", [_ev("e1", "k1")])
    repo = _RecordingRepo([c1], [_page("p1", ["c1"])])
    svc = _svc(repo, _FakeBlocks({}))
    result = svc.rebuild("k1", event="delete")
    assert result.committed is True
    assert repo.staged_claims["c1"].status is ClaimStatus.UNSUPPORTED
    assert repo.deleted_claims == []  # 不物理删除


def test_rebuild_cancel_is_cooperative():
    """cancel:已提交分事务保留一致,未处理跳过,cancelled=True。"""
    c1 = _claim("c1", [_ev("e1", "k1", block_id="b1", excerpt="OLD")])
    repo = _RecordingRepo([c1], [_page("p1", ["c1"])])
    svc = _svc(repo, _FakeBlocks({"b1": "NEW"}))
    job = RebuildJob()
    job.cancel()
    result = svc.rebuild("k1", event="update", job=job)
    assert result.cancelled is True
    assert result.committed is False


def test_rebuild_max_pages_truncates():
    """max_pages_per_job 截断:超限 page 不处理,truncated=True。"""
    claims = []
    pages = []
    for i in range(3):
        c = _claim(f"c{i}", [_ev(f"e{i}", "k1", block_id=f"b{i}", excerpt="OLD")])
        claims.append(c)
        pages.append(_page(f"p{i}", [f"c{i}"]))
    repo = _RecordingRepo(claims, pages)
    svc = _svc(repo, _FakeBlocks({f"b{i}": "NEW" for i in range(3)}))
    result = svc.rebuild("k1", event="update", max_pages_per_job=1)
    assert result.plan.truncated is True
    assert len(result.plan.affected_pages) <= 1
```

并加 `_RecordingRepo`（记录事务暂存 + 删除，模拟 `WikiRepository.transaction()`）到该测试文件：

```python
class _RecordingRepo(_FakeRepo):
    """模拟 WikiRepository 事务:记录 stage_claim/stage_page/delete。"""
    def __init__(self, claims, pages):
        super().__init__(claims, pages)
        self.staged_claims: dict[str, Claim] = {}
        self.staged_pages: dict[str, WikiPage] = {}
        self.deleted_claims: list[str] = []
    def transaction(self):
        outer = self
        class _Tx:
            def stage_claim(self, claim, expected_revision=None):
                outer.staged_claims[claim.claim_id] = claim
            def stage_page(self, page, expected_revision=None):
                outer.staged_pages[page.page_id] = page
            def commit(self):
                return []
        class _Ctx:
            def __enter__(self):
                return _Tx()
            def __exit__(self, *a):
                return False
        return _Ctx()
```

- [ ] **Step 2: 跑确认失败**

Run: `pytest tests/test_wiki_rebuild_service.py -v` → FAIL（`rebuild` / `RebuildJob` 不存在）。

- [ ] **Step 3: 实现 `rebuild` + `RebuildJob`**

追加到 `src/services/wiki_rebuild_service.py`：

```python
import threading


class RebuildJob:
    """rebuild 协作取消句柄(同步进程内)。"""
    def __init__(self) -> None:
        self._cancel = threading.Event()
    def cancel(self) -> None:
        self._cancel.set()
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()
```

并在 `WikiRebuildService` 类内加 `rebuild` 方法：

```python
    def rebuild(self, knowledge_id: str, *, event: str, job: "RebuildJob | None" = None,
                dry_run: bool = False, max_depth: int | None = None,
                max_pages_per_job: int | None = None) -> "RebuildResult":
        """按 plan 执行 staged rebuild。dry_run 只规划。cancel 协作生效。"""
        plan = self.plan_rebuild(knowledge_id, event=event, max_depth=max_depth,
                                 max_pages_per_job=max_pages_per_job)
        result = RebuildResult(knowledge_id=knowledge_id, event=event, plan=plan)
        if dry_run or (job is not None and job.cancelled):
            result.cancelled = bool(job and job.cancelled)
            return result

        now = self._clock()
        impacted_claim_ids = {c.claim_id for c in plan.affected_claims}
        impacted_page_ids = {p.page_id for p in plan.affected_pages}
        claims_by_id = {c.claim_id: c for c in self._repo.list_claims()}
        pages_by_id = {p.page_id: p for p in self._repo.list_pages()}
        stale_ev_ids = {e.evidence_id for e in plan.affected_evidence}

        with self._repo.transaction() as tx:
            for cid in sorted(impacted_claim_ids):
                if job is not None and job.cancelled:
                    break
                claim = claims_by_id.get(cid)
                if claim is None:
                    continue
                impact = next(c for c in plan.affected_claims if c.claim_id == cid)
                mutated = self._mutate_claim(claim, impact, stale_ev_ids, now)
                tx.stage_claim(mutated, expected_revision=claim.revision)
            if job is not None and job.cancelled:
                result.cancelled = True
                result.warnings.append("rebuild cancelled before projection refresh")
                return result
            for pid in sorted(impacted_page_ids):
                page = pages_by_id.get(pid)
                if page is None:
                    continue
                page.status = PageStatus.REVIEW
                page.updated_at = now
                tx.stage_page(page, expected_revision=page.revision)
            tx.commit()
        result.committed = True

        # projection 刷新(失败不回滚 canonical)
        try:
            self._projection.process_outbox()
            drift = self._projection.verify_parity()
            if drift:
                result.warnings.append(f"projection drift after rebuild: {len(drift)} findings")
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"projection refresh failed: {exc}")
        return result

    def _mutate_claim(self, claim: Claim, impact: "ClaimImpact",
                      stale_ev_ids: set[str], now: str) -> Claim:
        """标 stale evidence + 迁移 status。不 retract(保留审计)。"""
        for ev in claim.evidence:
            if ev.evidence_id in stale_ev_ids:
                ev.stale = True
                ev.stale_at = now
        if impact.proposed_status == "unsupported":
            claim.status = ClaimStatus.UNSUPPORTED
        claim.updated_at = now
        return claim
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_wiki_rebuild_service.py -v` → 全部 PASS（含 E2E-3/d03/cancel/max_pages）。

- [ ] **Step 5: container property + C6 守卫**

`src/core/container.py` 加 `_wiki_rebuild_service` 字段 + property：

```python
    @property
    def wiki_rebuild_service(self):
        if self._wiki_rebuild_service is None:
            from src.services.wiki_rebuild_service import WikiRebuildService as _RB
            self._wiki_rebuild_service = _RB(
                repository=self.wiki_repository,
                projection=self.wiki_projection,
                block_repository=self.block_repo,
                dependency_service=self.wiki_dependency_service,
                config=self.config,
            )
            self._track_service("_wiki_rebuild_service")
        return self._wiki_rebuild_service
```

`tests/test_canonical_write_guards.py` 的 `WIKI_V2_SERVICE_MODULES` 加 `"services/wiki_rebuild_service.py"`。

- [ ] **Step 6: ruff + mypy + 守卫 + commit**

Run: `ruff check src tests && mypy src && pytest tests/test_canonical_write_guards.py -q` → PASS。

```bash
git add src/services/wiki_rebuild_service.py src/core/container.py \
        tests/test_wiki_rebuild_service.py tests/test_canonical_write_guards.py
git commit -m "feat(wiki-v2): propagate source changes through affected knowledge"
```

---

## Task T5.2c：wiki_dependencies 表投影（read model）

**Files:**
- Modify: `src/services/wiki_projection.py`（`_upsert_claim` / `_upsert_page` 同步写边 + `rebuild` 重灌）
- Modify: `tests/test_wiki_projection.py`

**Interfaces:**
- Produces: 投影时把 `source→evidence→claim→page` + `claim→claim` 边写入 `wiki_dependencies`（先删该对象相关边再插，幂等）；`rebuild()` 已清空该表（`_clear_v2_tables` 已含），重灌时一并写边。

- [ ] **Step 1: 写失败测试（边投影 + rebuild 重灌）**

追加到 `tests/test_wiki_projection.py`：

```python
def test_projection_writes_dependency_edges(tmp_path):
    """_upsert_claim + _upsert_page 后,wiki_dependencies 含 source/evidence/claim/page 边。"""
    from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance, WikiPage, PageType, PageStatus
    from src.services.wiki_projection import WikiProjection
    db = _build_v2_db(tmp_path)
    repo = _build_repo(tmp_path)
    proj = WikiProjection(repository=repo, database=db, enabled=True)
    ev = Evidence(evidence_id="ev1", stance=EvidenceStance.SUPPORTS, knowledge_id="k1", block_id="b1")
    claim = _make_claim(claim_id="c1", evidence=[ev],
                        relations=[{"relation": "refines", "target_claim_id": "c0"}])
    proj._upsert_claim(claim)
    page = _make_page(page_id="p1", claim_ids=["c1"])
    proj._upsert_page(page, path="concepts/p1.md")
    conn = db.get_conn()
    rows = {(r["from_type"], r["from_id"], r["to_type"], r["to_id"], r["relation"])
            for r in conn.execute("SELECT * FROM wiki_dependencies")}
    assert ("source", "k1", "evidence", "ev1", "produces") in rows
    assert ("evidence", "ev1", "claim", "c1", "evidences") in rows
    assert ("claim", "c1", "claim", "c0", "refines") in rows
    assert ("claim", "c1", "page", "p1", "cited_in") in rows


def test_projection_rebuild_repopulates_dependencies(tmp_path):
    """rebuild() 清空后重灌 wiki_dependencies。"""
    from src.services.wiki_projection import WikiProjection
    db = _build_v2_db(tmp_path)
    repo = _build_repo_with_one_claim(tmp_path)  # 复用本文件 helper:含 1 claim+page
    proj = WikiProjection(repository=repo, database=db, enabled=True)
    proj._clear_v2_tables()
    assert db.get_conn().execute("SELECT COUNT(*) AS c FROM wiki_dependencies").fetchone()["c"] == 0
    proj.rebuild()
    assert db.get_conn().execute("SELECT COUNT(*) AS c FROM wiki_dependencies").fetchone()["c"] >= 1
```

> `tests/test_wiki_projection.py` 内新增 helper（若同名已存在则扩展）：
>
> ```python
> def _make_claim(claim_id, evidence, relations=None, status=None):
>     from src.models.wiki_v2 import Claim, ClaimStatus, ClaimRelation
>     return Claim(schema_version=1, claim_id=claim_id, statement=claim_id,
>                  normalized_statement=claim_id, claim_type="fact",
>                  status=status or ClaimStatus.ACTIVE, confidence=0.9,
>                  valid_from=None, valid_to=None, subject_refs=["s"], predicate="p",
>                  object_refs=["o"], evidence=evidence,
>                  relations=[ClaimRelation(r["relation"], r["target_claim_id"]) for r in (relations or [])],
>                  created_at="t", updated_at="t", revision=1)
>
> def _make_page(page_id, claim_ids, status=None):
>     from src.models.wiki_v2 import WikiPage, PageType, PageStatus
>     return WikiPage(schema_version=1, page_id=page_id, title=page_id, page_type=PageType.CONCEPTS,
>                     status=status or PageStatus.PUBLISHED, revision=1, aliases=[], tags=[],
>                     source_ids=[], claim_ids=claim_ids, created_at="t", updated_at="t",
>                     content_hash="ch", body="")
>
> def _build_repo_with_one_claim(tmp_path):
>     from src.services.wiki_repository import WikiRepository
>     from src.models.wiki_v2 import Evidence, EvidenceStance
>     repo = WikiRepository(wiki_dir=tmp_path/"wiki",
>                           registry_path=tmp_path/"wiki/_meta/pages.json",
>                           redirects_path=tmp_path/"wiki/_meta/redirects.json",
>                           outbox_path=tmp_path/"outbox.jsonl")
>     ev = Evidence(evidence_id="ev1", stance=EvidenceStance.SUPPORTS, knowledge_id="k1", block_id="b1")
>     with repo.transaction() as tx:
>         tx.stage_claim(_make_claim("c1", [ev]))
>         tx.stage_page(_make_page("p1", ["c1"]))
>         tx.commit()
>     return repo
> ```

- [ ] **Step 2: 跑确认失败**

Run: `pytest tests/test_wiki_projection.py::test_projection_writes_dependency_edges tests/test_wiki_projection.py::test_projection_rebuild_repopulates_dependencies -v` → FAIL。

- [ ] **Step 3: 实现边投影**

`src/services/wiki_projection.py`：

`_upsert_claim` 末尾（evidence 写完后）加边写入：

```python
        # Phase 5:依赖图边(read model)。先删该 claim 相关边再插(幂等)。
        conn.execute("DELETE FROM wiki_dependencies WHERE from_id = ? OR to_id = ?",
                     (claim.claim_id, claim.claim_id))
        edge_rows = []
        for ev in claim.evidence:
            edge_rows.append(("source", ev.knowledge_id, "evidence", ev.evidence_id, "produces"))
            edge_rows.append(("evidence", ev.evidence_id, "claim", claim.claim_id, "evidences"))
        for rel in claim.relations:
            edge_rows.append(("claim", claim.claim_id, "claim", rel.target_claim_id, rel.relation))
        if edge_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO wiki_dependencies(from_type,from_id,to_type,to_id,relation) "
                "VALUES (?,?,?,?,?)", edge_rows,
            )
```

`_upsert_page` 末尾（`wiki_page_claims` 写完后）加：

```python
        # Phase 5:claim→page 边。先删该 page 的 cited_in 边再插。
        conn.execute(
            "DELETE FROM wiki_dependencies WHERE to_type='page' AND to_id = ?", (page.page_id,))
        if page.claim_ids:
            conn.executemany(
                "INSERT OR IGNORE INTO wiki_dependencies(from_type,from_id,to_type,to_id,relation) "
                "VALUES ('claim',?, 'page', ?, 'cited_in')",
                [(cid, page.page_id) for cid in page.claim_ids],
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_wiki_projection.py -q` → PASS。

- [ ] **Step 5: ruff + mypy + commit**

Run: `ruff check src tests && mypy src` → 0。

```bash
git add src/services/wiki_projection.py tests/test_wiki_projection.py
git commit -m "feat(wiki-v2): project dependency graph as read model"
```

---

## Task T5.3a：RebuildScheduler（per-kid debounce）

**Files:**
- Create: `src/services/wiki_rebuild_scheduler.py`
- Create: `tests/test_wiki_rebuild_scheduler.py`
- Modify: `src/core/container.py`（`wiki_rebuild_scheduler` property）
- Modify: `tests/test_canonical_write_guards.py`（C6 覆盖）

**Interfaces:**
- Consumes: `WikiRebuildService.rebuild(knowledge_id, *, event)`（T5.2b）。
- Produces:
  - `RebuildScheduler(rebuild_service, debounce_ms: int = 500)`
  - `schedule(knowledge_id: str, event_type: str) -> None`，`event_type ∈ {"update","delete"}`
  - `flush() -> RebuildBatchResult`、`pending_count -> int`

- [ ] **Step 1: 写失败测试（合并语义）**

`tests/test_wiki_rebuild_scheduler.py`：

```python
"""RebuildScheduler per-kid debounce 合并测试。"""
from src.services.wiki_rebuild_scheduler import RebuildScheduler


class _FakeRebuild:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
    def rebuild(self, knowledge_id, *, event, **kw):
        self.calls.append((knowledge_id, event))
        return type("R", (), {"committed": True, "cancelled": False, "warnings": []})()


def test_update_update_merges_to_single_update():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    sch.schedule("k1", "update")
    sch.flush()
    assert svc.calls == [("k1", "update")]


def test_update_then_delete_merges_to_delete():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    sch.schedule("k1", "delete")  # delete 主导
    sch.flush()
    assert svc.calls == [("k1", "delete")]


def test_distinct_kids_not_merged():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    sch.schedule("k2", "delete")
    assert sch.pending_count == 2
    sch.flush()
    assert sorted(svc.calls) == [("k1", "update"), ("k2", "delete")]


def test_pending_count_drops_drop_events():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    sch.schedule("k1", "delete")
    sch.schedule("k1", "update")  # delete + update → delete(不 drop)
    assert sch.pending_count == 1
```

- [ ] **Step 2: 跑确认失败**

Run: `pytest tests/test_wiki_rebuild_scheduler.py -v` → FAIL（模块不存在）。

- [ ] **Step 3: 实现 `RebuildScheduler`**

`src/services/wiki_rebuild_scheduler.py`：

```python
"""RebuildScheduler — per-kid debounce 合并 source 变更事件(Phase 5)。

合并范式对齐 IndexScheduler,但语义是知识失效传播(非原始索引):
  - update + update → update
  - update + delete → delete(delete 主导)
  - delete + update → delete
  - distinct kid 不合并
flush() 对每个 pending kid 调 rebuild_service.rebuild。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RebuildBatchResult:
    processed: int = 0
    failed: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class RebuildScheduler:
    def __init__(self, rebuild_service: Any, debounce_ms: int = 500) -> None:
        self._svc = rebuild_service
        self._debounce_ms = debounce_ms
        self._pending: dict[str, str] = {}  # knowledge_id -> event
        self._lock = threading.Lock()

    def schedule(self, knowledge_id: str, event_type: str) -> None:
        if event_type not in ("update", "delete"):
            return
        with self._lock:
            prev = self._pending.get(knowledge_id)
            self._pending[knowledge_id] = self._merge(prev, event_type)

    @staticmethod
    def _merge(prev: str | None, new: str) -> str:
        if prev is None:
            return new
        # delete 主导:任一端 delete → delete
        if "delete" in (prev, new):
            return "delete"
        return new  # update + update → update

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def flush(self) -> RebuildBatchResult:
        with self._lock:
            events = dict(self._pending)
            self._pending.clear()
        result = RebuildBatchResult()
        for kid in sorted(events):  # 字典序确定性
            try:
                self._svc.rebuild(knowledge_id=kid, event=events[kid])
                result.processed += 1
            except Exception as exc:  # noqa: BLE001
                result.failed.append({"knowledge_id": kid, "error": str(exc)})
        return result
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_wiki_rebuild_scheduler.py -v` → 4 例 PASS。

- [ ] **Step 5: container property + C6 守卫**

`src/core/container.py` 加 `_wiki_rebuild_scheduler` 字段 + property（注入 `self.wiki_rebuild_service` 与 `self.config.get("wiki.rebuild.debounce_ms", 500)`）：

```python
    @property
    def wiki_rebuild_scheduler(self):
        if self._wiki_rebuild_scheduler is None:
            from src.services.wiki_rebuild_scheduler import RebuildScheduler as _Sched
            self._wiki_rebuild_scheduler = _Sched(
                rebuild_service=self.wiki_rebuild_service,
                debounce_ms=int(self.config.get("wiki.rebuild.debounce_ms", 500)),
            )
            self._track_service("_wiki_rebuild_scheduler")
        return self._wiki_rebuild_scheduler
```

`tests/test_canonical_write_guards.py` 的 `WIKI_V2_SERVICE_MODULES` 加 `"services/wiki_rebuild_scheduler.py"`。

- [ ] **Step 6: ruff + mypy + 守卫 + commit**

Run: `ruff check src tests && mypy src && pytest tests/test_canonical_write_guards.py -q` → PASS。

```bash
git add src/services/wiki_rebuild_scheduler.py src/core/container.py \
        tests/test_wiki_rebuild_scheduler.py tests/test_canonical_write_guards.py
git commit -m "feat(wiki-v2): add rebuild debounce scheduler"
```

---

## Task T5.3b：触发接入（workflow 门控 + watcher + CLI）

**Files:**
- Modify: `src/services/knowledge_workflow.py`
- Modify: `src/services/path_indexer.py` / `src/services/file_watcher.py`
- Modify: `src/cli.py`
- Modify: `config.example.yaml`
- Modify: `tests/test_knowledge_workflow.py`（门控测试）

**Interfaces:**
- Consumes: `RebuildScheduler.schedule()`（T5.3a）、`resolve_canonical_mode(config)`（已有）、`rebuild.auto_on_source_update` / `rebuild.auto_allowlist`（config）。
- Produces:
  - `KnowledgeWorkflowService.compile()` 在 primary 模式 + 门控命中时调 `rebuild_scheduler.schedule(kid, event)`
  - CLI `shinehe rebuild --knowledge-id <id> [--event update|delete] [--dry-run]`

- [ ] **Step 1: 写门控失败测试**

`tests/test_knowledge_workflow.py` 追加：

```python
def test_compile_schedules_rebuild_only_when_auto_enabled(monkeypatch, tmp_path):
    """primary 模式 + auto_on_source_update=true → compile 后 scheduler 收到 update;
    auto_on_source_update=false(默认)→ 不 schedule。"""
    # 构造最小 KnowledgeWorkflowService + mock rebuild_scheduler
    # (沿用本文件已有 fixture 注入 shadow/canary/primary workflow 的模式)
    scheduled = []
    class _Sched:
        def schedule(self, kid, ev):
            scheduled.append((kid, ev))
    svc = _build_workflow(tmp_path, mode="primary",
                          rebuild_scheduler=_Sched(),
                          config={"wiki.rebuild.auto_on_source_update": True})
    svc.compile(knowledge_id="k1", item={"source_path": "x.md", "content_hash": "v2"})
    assert ("k1", "update") in scheduled

    scheduled.clear()
    svc_off = _build_workflow(tmp_path, mode="primary", rebuild_scheduler=_Sched(),
                              config={"wiki.rebuild.auto_on_source_update": False})
    svc_off.compile(knowledge_id="k1", item={"source_path": "x.md", "content_hash": "v2"})
    assert scheduled == []  # 默认 off
```

> `_build_workflow` helper：沿用本文件已有 `KnowledgeWorkflowService` 构造模式，新增可注入 `rebuild_scheduler` 与 `config` 覆盖。

- [ ] **Step 2: 跑确认失败**

Run: `pytest tests/test_knowledge_workflow.py::test_compile_schedules_rebuild_only_when_auto_enabled -v` → FAIL。

- [ ] **Step 3: knowledge_workflow 接入门控**

`src/services/knowledge_workflow.py` 的 `KnowledgeWorkflowService.__init__` 加可选 `rebuild_scheduler=None`（构造函数注入；container 传入 `self.rebuild_scheduler`，测试传 mock）。在 `compile()` raw 索引成功后、primary 路径末尾加：

```python
        # Phase 5:门控触发 rebuild scheduler(仅 primary + auto 或 canary allowlist)
        if self._rebuild_scheduler is not None and self._mode_is_primary_or_canary(knowledge_id, item):
            self._rebuild_scheduler.schedule(knowledge_id, "update")
```

并加 helper：

```python
    def _mode_is_primary_or_canary(self, knowledge_id, item):
        from src.services.wiki_query_service import resolve_canonical_mode
        mode = resolve_canonical_mode(self._config)
        if mode != "primary":
            return False
        if self._cfg("wiki.rebuild.auto_on_source_update", False):
            return True
        # canary:命中 rebuild.auto_allowlist
        allow_kid = set(self._cfg_list("wiki.rebuild.auto_allowlist.knowledge_ids"))
        allow_paths = self._cfg_list("wiki.rebuild.auto_allowlist.source_paths")
        if knowledge_id in allow_kid:
            return True
        src = str((item or {}).get("source_path") or "").replace("\\", "/")
        return any(src == p.rstrip("/") or src.startswith(f"{p.rstrip('/')}/") for p in allow_paths)
```

> `_cfg` / `_cfg_list`：执行前先 `grep "def _cfg" src/services/knowledge_workflow.py` 核实是否已有；若已有则复用其签名；若无，按 `src/services/wiki_canary_workflow.py:55-58`（`_cfg`）与 `:144-152`（`_cfg_list`）的实现照搬到 `KnowledgeWorkflowService`。

`src/core/container.py` 的 `KnowledgeWorkflowService` 构造（line ~361）加 `rebuild_scheduler=self.wiki_rebuild_scheduler`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_knowledge_workflow.py -q` → PASS。

- [ ] **Step 5: file_watcher / path_indexer 接入（冒烟级）**

在 `file_watcher.py` 文件删除事件回调中（已存在 delete 检测处），raw 删除成功后加：

```python
        # Phase 5:来源删除 → 通知 rebuild scheduler(若容器已装配)
        try:
            scheduler = self._rebuild_scheduler  # 构造函数注入(可选)
            if scheduler is not None:
                scheduler.schedule(deleted_knowledge_id, "delete")
        except Exception:  # noqa: BLE001  watcher 失败不阻断主进程
            pass
```

`path_indexer.py` 的 `delete_path` 返回 deleted knowledge_id 后同理（若 file_watcher 已覆盖，可只在 watcher 接）。

- [ ] **Step 6: CLI `shinehe rebuild`**

`src/cli.py` 加子命令（沿用现有 click/typer 风格）：

```python
@cli.command("rebuild")
@click.option("--knowledge-id", required=True)
@click.option("--event", default="update", type=click.Choice(["update", "delete"]))
@click.option("--dry-run", is_flag=True)
def rebuild_cmd(knowledge_id, event, dry_run):
    """Phase 5:手动触发某 source 的依赖失效重建。"""
    container = create_container()
    if dry_run:
        plan = container.wiki_rebuild_service.plan_rebuild(knowledge_id, event=event)
        click.echo(plan.stats)
        return
    result = container.wiki_rebuild_service.rebuild(knowledge_id, event=event)
    click.echo({"committed": result.committed, "cancelled": result.cancelled,
                "warnings": result.warnings, "stats": result.plan.stats})
```

- [ ] **Step 7: config.example.yaml 补项**

`wiki.rebuild` 段加：

```yaml
  rebuild:
    auto_on_source_update: false
    auto_publish_low_risk: false
    max_pages_per_job: 100
    max_depth: 5
    debounce_ms: 500
    auto_allowlist:
      knowledge_ids: []
      source_paths: []
```

- [ ] **Step 8: ruff + mypy + 相关测试 + commit**

Run: `ruff check src tests && mypy src && pytest tests/test_knowledge_workflow.py tests/test_wiki_rebuild_service.py -q` → PASS。

```bash
git add src/services/knowledge_workflow.py src/services/file_watcher.py \
        src/services/path_indexer.py src/cli.py src/core/container.py \
        config.example.yaml tests/test_knowledge_workflow.py
git commit -m "feat(wiki-v2): trigger incremental rebuild from source changes"
```

---

## Task T5.4：启用黄金集 + E2E-3/E2E-4 + 全量门禁 + 文档

**Files:**
- Modify: `tests/test_wiki_v2_golden_eval.py`（启用 source_update/source_delete）
- Create: `tests/test_wiki_v2_phase5_e2e.py`（E2E-3/E2E-4 真实 repo 集成）
- Modify: `PROGRESS.md`、Create: `docs/superpowers/reviews/2026-07-13-phase5-review.md`

**Interfaces:**
- Consumes: 黄金集 `evals/wiki_v2/source_update.jsonl` + `source_delete.jsonl`；真实 `WikiRepository` + 临时 `wiki_dir`。

- [ ] **Step 1: 写 E2E-3 集成测试（真实事务）**

`tests/test_wiki_v2_phase5_e2e.py`：

```python
"""Phase 5 E2E:E2E-3(来源更新) + E2E-4(来源删除仍有他源)真实 WikiRepository 集成。"""
import pytest

from src.models.wiki_v2 import ClaimStatus, EvidenceStance, PageStatus
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_dependency_service import WikiDependencyService
from src.services.wiki_rebuild_service import WikiRebuildService
from src.services.wiki_repository import WikiRepository


def _build_real_repo(tmp_path):
    return WikiRepository(
        wiki_dir=tmp_path / "wiki",
        registry_path=tmp_path / "wiki/_meta/pages.json",
        redirects_path=tmp_path / "wiki/_meta/redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )


def _seed_claim_with_page(repo, claim_id, evidence, page_id):
    """用真实事务 seed 一条 active claim + 一张 published page 引用它。"""
    from src.models.wiki_v2 import Claim, WikiPage, PageType
    claim = Claim(schema_version=1, claim_id=claim_id, statement=claim_id,
                  normalized_statement=claim_id, claim_type="fact",
                  status=ClaimStatus.ACTIVE, confidence=0.9, valid_from=None, valid_to=None,
                  subject_refs=["s"], predicate="p", object_refs=["o"], evidence=evidence,
                  relations=[], created_at="t", updated_at="t", revision=0)
    page = WikiPage(schema_version=1, page_id=page_id, title=page_id, page_type=PageType.CONCEPTS,
                    status=PageStatus.PUBLISHED, revision=0, aliases=[], tags=[], source_ids=[],
                    claim_ids=[claim_id], created_at="t", updated_at="t", content_hash="ch", body="")
    with repo.transaction() as tx:
        tx.stage_claim(claim)
        tx.stage_page(page)
        tx.commit()


class _FakeBlocks:
    def __init__(self, mapping):  # {block_id: content}
        self._m = mapping
    def list_by_page(self, page_id, limit=10000):
        from src.models.block import Block
        return [Block(id=bid, page_id=page_id, content=c) for bid, c in self._m.items()]


class _NoopProjection:
    enabled = True
    def process_outbox(self, *, force=False):
        return type("R", (), {"processed": 0, "skipped": 0, "warnings": [], "errors": []})()
    def verify_parity(self):
        return []


def test_e2e3_source_update_orphan_block_makes_claim_unsupported(tmp_path):
    """E2E-3:A v1 支持 c1 → A v2 删段(block 消失)→ evidence stale → c1 unsupported → page review。"""
    from src.models.wiki_v2 import Evidence
    repo = _build_real_repo(tmp_path)
    old_hash = compute_excerpt_hash("original content")
    ev = Evidence(evidence_id="evA", stance=EvidenceStance.SUPPORTS, knowledge_id="kA",
                  block_id="b1", source_revision="v1", excerpt_hash=old_hash)
    _seed_claim_with_page(repo, "c1", [ev], "p1")
    dep = WikiDependencyService(repository=repo)
    svc = WikiRebuildService(repository=repo, projection=_NoopProjection(),
                             block_repository=_FakeBlocks({}),  # b1 消失
                             dependency_service=dep, config={"wiki.rebuild.max_pages_per_job": 100,
                                                             "wiki.rebuild.max_depth": 5},
                             clock=lambda: "NOW")
    result = svc.rebuild("kA", event="update")
    assert result.committed is True
    after = repo.get_claim("c1")
    assert after.status is ClaimStatus.UNSUPPORTED
    assert after.evidence[0].stale is True
    assert repo.get_page("p1").status is PageStatus.REVIEW
    # d03:claim 仍存在(不物理删除)
    assert after is not None


def test_e2e4_source_delete_with_other_supports_stays_active(tmp_path):
    """E2E-4:A、B 均支持 c1 → 删 A → c1 仍 active(剩 B 的 evidence)。"""
    from src.models.wiki_v2 import Evidence
    repo = _build_real_repo(tmp_path)
    ev_a = Evidence(evidence_id="evA", stance=EvidenceStance.SUPPORTS, knowledge_id="kA",
                    block_id="bA", source_revision="v1", excerpt_hash="hA")
    ev_b = Evidence(evidence_id="evB", stance=EvidenceStance.SUPPORTS, knowledge_id="kB",
                    block_id="bB", source_revision="v1", excerpt_hash="hB")
    _seed_claim_with_page(repo, "c1", [ev_a, ev_b], "p1")
    dep = WikiDependencyService(repository=repo)
    svc = WikiRebuildService(repository=repo, projection=_NoopProjection(),
                             block_repository=_FakeBlocks({}),
                             dependency_service=dep, config={"wiki.rebuild.max_pages_per_job": 100,
                                                             "wiki.rebuild.max_depth": 5},
                             clock=lambda: "NOW")
    result = svc.rebuild("kA", event="delete")
    assert result.committed is True
    after = repo.get_claim("c1")
    assert after.status is ClaimStatus.ACTIVE  # 仍有 evB
    # evA 标 stale,evB 未受影响
    evA = next(e for e in after.evidence if e.evidence_id == "evA")
    evB = next(e for e in after.evidence if e.evidence_id == "evB")
    assert evA.stale is True
    assert evB.stale is False
```

- [ ] **Step 2: 跑 E2E 确认通过**

Run: `pytest tests/test_wiki_v2_phase5_e2e.py -v` → 2 例 PASS（若红灯，按 systematic-debugging 排查，不放宽断言）。

- [ ] **Step 3: 启用 C2 黄金集 source_update/source_delete**

`tests/test_wiki_v2_golden_eval.py`：已有加载 `claim_matching/merge/extraction` 的机制，参照其模式加载数据驱动的 source_update/source_delete 断言。由于黄金集 jsonl 当前只有 scenario 描述（非可执行断言），本 step 实现：新增一个参数化测试，用 `WikiRebuildService` 在受控 fixture 上验证每条 scenario 的 `expected` 文本关键断言（u01:unchanged retained、u02:stale+review、u03:no rebuild、d01:active、d02:unsupported、d03:not deleted）：

```python
@pytest.mark.parametrize("dataset,event", [
    ("source_update.jsonl", "update"), ("source_delete.jsonl", "delete"),
])
def test_source_evolution_golden(dataset, event, tmp_path):
    """C2 source_update/source_delete 黄金集:逐 scenario 行为断言。"""
    import json
    from pathlib import Path
    cases = [json.loads(ln) for ln in
             (Path("evals/wiki_v2") / dataset).read_text(encoding="utf-8").splitlines() if ln.strip()]
    repo = _build_real_repo(tmp_path)
    dep = WikiDependencyService(repository=repo)
    svc = WikiRebuildService(repository=repo, projection=_NoopProjection(),
                             block_repository=_FakeBlocksForCases(cases),
                             dependency_service=dep,
                             config={"wiki.rebuild.max_pages_per_job": 100, "wiki.rebuild.max_depth": 5},
                             clock=lambda: "NOW")
    for case in cases:
        plan = svc.plan_rebuild(case["trigger"]["knowledge_id"], event=event)
        exp = case["expected"]
        if "unchanged" in exp:
            assert plan.affected_evidence == [], case["id"]
        if "stale" in exp:
            assert any(e.reason in ("block_changed", "block_deleted", "source_deleted")
                       for e in plan.affected_evidence), case["id"]
        if "active" in exp:
            assert all(c.proposed_status == "active" for c in plan.affected_claims), case["id"]
        if "unsupported" in exp:
            assert any(c.proposed_status == "unsupported" for c in plan.affected_claims), case["id"]
```

> `_FakeBlocksForCases`：按 case 的 `changed_blocks` 构造当前 block 集合（changed 的 block 给新 content，其余保留旧 hash）。在测试文件内实现。

- [ ] **Step 4: 跑黄金集确认通过**

Run: `pytest tests/test_wiki_v2_golden_eval.py -v` → 含新 source 演化用例 PASS。

- [ ] **Step 5: 全量门禁**

Run（按纠偏方案纪律，逐项）：
```bash
pytest -q                              # 期望 ≥ 1455 passed / 2 skipped / 5 xfailed（C2 xfail 不变）
ruff check src tests evals tools scripts
mypy src tools
python evals/run_retrieval_eval.py --all   # Overall PASS
python evals/run_wiki_eval.py              # 指标不退化
```

任何一项退化 → 停下，systematic-debugging，不放宽阈值/不扩 allowlist。

- [ ] **Step 6: 更新 PROGRESS + 写 review**

`PROGRESS.md` 顶部新增「Canonical Wiki V2 Phase 5 依赖图与失效传播 — 验收通过（2026-07-13）」段：列表 T5.0-T5.4 交付、验证结果（pytest 计数、ruff/mypy、retrieval/wiki eval 指标）、commit 列表、Phase 6 待开始。

`docs/superpowers/reviews/2026-07-13-phase5-review.md`：参照 `2026-07-13-phase4c-primary-review.md` 结构——Scope/Result、Delivered、Findings、Residual Risks for Phase 6、Verification（贴实际命令输出）。

- [ ] **Step 7: Commit**

```bash
git add tests/test_wiki_v2_phase5_e2e.py tests/test_wiki_v2_golden_eval.py \
        PROGRESS.md docs/superpowers/reviews/2026-07-13-phase5-review.md
git commit -m "test(wiki-v2): enable source evolution golden evaluation"
```

---

## 完成定义（Phase 5 收尾）

- [ ] T5.0-T5.4 全部 commit 落盘
- [ ] E2E-3 / E2E-4 集成测试通过
- [ ] C2 source_update / source_delete 黄金集启用并通过
- [ ] 环检测 / max_depth / max_pages / cancel 各有测试
- [ ] `wiki_dependencies` 表可重建、parity 100%
- [ ] rebuild 中途崩溃可 `recover()` 到一致（复用 C3 测试，可选追加 rebuild 段故障注入）
- [ ] 三级门控（manual / canary / auto）默认 off
- [ ] 3 新服务构造函数 DI，C6 守卫覆盖
- [ ] guard allowlist 空、扫描范围未收缩
- [ ] 全量 pytest / ruff / mypy / retrieval eval / wiki eval 不退化
- [ ] C2 的 5 个 xfail 原样保留
- [ ] PROGRESS + review 文档更新
- [ ] 完成后调用 `superpowers:verification-before-completion` 自证，再 `superpowers:requesting-code-review`
