# Canonical Wiki V2 Phase 4C Primary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch Canonical Wiki V2 from canary to primary write paths while preserving compatibility fields and shrinking direct-write guard allowlists.

**Architecture:** Phase 4C replaces legacy direct wiki writes one entrypoint at a time with `WikiRepository` transactions and projection outbox semantics. The safest first cut is query-save/write-service primary mode, then ingest workflow primary mode, then compatibility adapters and guard allowlist removal.

**Tech Stack:** Python, pytest, `WikiRepository`, `WikiProjection`, `WikiWriteService`, `KnowledgeWorkflowService`, `WikiQueryService`, canonical write guard tests.

---

## File Structure

- `src/services/wiki_write_service.py`: primary-mode query save compatibility facade; writes `WikiPage` through `WikiRepository`.
- `src/core/container.py`: inject `wiki_repository`, `wiki_projection`, and config into `WikiWriteService`.
- `src/services/knowledge_workflow.py`: primary-mode ingest orchestration; eventually skips legacy FS compilers and runs formal V2 workflow.
- `src/services/wiki_canary_workflow.py`: provide an allow-all primary execution mode or a reusable base for `primary`.
- `src/services/wiki_compiler.py`: compatibility adapter for old API calls after primary write paths exist.
- `src/services/wiki_entity_updater.py`: convert direct writer into suggestion producer.
- `tests/test_wiki_write_service.py`: primary query-save facade tests.
- `tests/test_knowledge_workflow.py`: primary-mode workflow routing tests.
- `tests/test_canonical_write_guards.py`: remove allowlist entries as direct writes disappear.
- `PROGRESS.md`: update only when a full Phase 4C gate is met.
- `docs/superpowers/reviews/2026-07-09-phase4c-primary-review.md`: create at the end of Phase 4C before commit.

## Task 1: WikiWriteService Primary Query Save

**Files:**
- Modify: `src/services/wiki_write_service.py`
- Modify: `src/core/container.py`
- Modify: `tests/test_wiki_write_service.py`

- [x] **Step 1: Write the failing primary-mode test**

Add this test to `tests/test_wiki_write_service.py`:

```python
from src.models.wiki_v2 import PageStatus, PageType


class _FakeConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _FakeCanonicalRepo:
    def __init__(self):
        self.saved_pages = []

    def save_page(self, page, expected_revision=None):
        self.saved_pages.append((page, expected_revision))
        return type("SaveResult", (), {"ok": True, "object_id": page.page_id, "revision": 1})()


class _FakeProjection:
    def __init__(self):
        self.processed = 0

    def process_outbox(self):
        self.processed += 1
        return type("ProjectionResult", (), {"processed": 1, "errors": [], "warnings": []})()


def test_primary_save_uses_canonical_repository_without_legacy_double_write():
    compiler = _FakeCompiler()
    workflow = _FakeWorkflow()
    repo = _FakeCanonicalRepo()
    projection = _FakeProjection()
    svc = WikiWriteService(
        compiler,
        workflow,
        repository=repo,
        projection=projection,
        config=_FakeConfig({"wiki.canonical_v2.mode": "primary"}),
    )

    result = svc.save("What is FTTR?", "FTTR answer body" + "x" * 120, ["k1"], confidence=0.8, timestamp="2026-07-09T12:00:00")

    assert result["sqlite_page_id"] is None
    assert result["fs_saved"] is False
    assert result["canonical_saved"] is True
    assert result["page_id"].startswith("page_")
    assert result["projection_pending"] is False
    assert result["projection_processed"] == 1
    assert compiler.called is None
    assert workflow.called is None
    page = repo.saved_pages[0][0]
    assert page.page_type == PageType.SYNTHESES
    assert page.status == PageStatus.DRAFT
    assert page.source_ids == ["k1"]
    assert "FTTR answer body" in page.body
```

- [x] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_wiki_write_service.py::test_primary_save_uses_canonical_repository_without_legacy_double_write -q
```

Expected: fail because `WikiWriteService.__init__` does not accept `repository`, `projection`, or `config`, and `save()` always double-writes legacy tracks.

- [x] **Step 3: Implement minimal primary path**

In `src/services/wiki_write_service.py`, add optional constructor dependencies:

```python
def __init__(self, wiki_compiler, knowledge_workflow, repository=None, projection=None, config=None):
    self._compiler = wiki_compiler
    self._workflow = knowledge_workflow
    self._repo = repository
    self._projection = projection
    self._config = config
```

Add a `_cfg()` helper and mode check:

```python
def _cfg(self, key: str, default=None):
    if self._config is not None:
        return self._config.get(key, default)
    from src.utils.config import Config
    return Config.get(key, default)
```

In `save()`, before legacy writes, if `wiki.canonical_v2.mode == "primary"` and `self._repo` is available, construct a `WikiPage` with:

- `page_id = f"page_{uuid.uuid4()}"`
- `title = question[:120].strip() or "Untitled Query"`
- `page_type = PageType.SYNTHESES`
- `status = PageStatus.DRAFT`
- `source_ids = source_ids or []`
- `body = f"# {title}\n\n{answer}\n"`
- `content_hash = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()`

Save it through `self._repo.save_page(page, expected_revision=None)`, optionally call `self._projection.process_outbox()`, and return a compatibility result containing:

```python
{
    "sqlite_page_id": None,
    "fs_saved": False,
    "errors": [],
    "page_id": page.page_id,
    "canonical_saved": True,
    "projection_pending": False,
    "projection_processed": processed_count,
}
```

- [x] **Step 4: Run the primary test**

Run:

```bash
pytest tests/test_wiki_write_service.py::test_primary_save_uses_canonical_repository_without_legacy_double_write -q
```

Expected: pass.

- [x] **Step 5: Run existing write-service tests**

Run:

```bash
pytest tests/test_wiki_write_service.py -q
```

Expected: existing legacy double-write behavior still passes when mode is not `primary`.

- [x] **Step 6: Inject dependencies from the container**

Modify `src/core/container.py` `wiki_write_service` property to instantiate:

```python
self._wiki_write_service = WikiWriteService(
    wiki_compiler=self.wiki_compiler,
    knowledge_workflow=self.knowledge_workflow,
    repository=self.wiki_repository,
    projection=self.wiki_projection,
    config=self.config,
)
```

- [x] **Step 7: Run container and service tests**

Run:

```bash
pytest tests/test_wiki_write_service.py tests/test_knowledge_workflow.py tests/test_wiki_canonical_mode.py -q
```

Expected: pass.

## Task 2: Primary Ingest Workflow Routing

**Files:**
- Modify: `src/services/knowledge_workflow.py`
- Modify: `src/services/wiki_canary_workflow.py` or create `src/services/wiki_primary_workflow.py`
- Modify: `src/core/container.py`
- Test: `tests/test_knowledge_workflow.py`

- [x] **Step 1: Write the failing primary routing test**

Add to `tests/test_knowledge_workflow.py`:

```python
def test_compile_primary_mode_runs_primary_without_legacy_compilers():
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "primary")
    _insert_knowledge()
    fakes = FakeCompilers()
    primary = MagicMock()
    primary.run.return_value = {"status": "completed", "tx_id": "tx_primary", "new_claims": 1}

    result = KnowledgeWorkflowService(
        source_compiler=fakes.source,
        entity_updater=fakes.entity,
        index_compiler=fakes.index,
        log_compiler=fakes.log,
        primary_workflow=primary,
    ).compile("kid-1", ingested_at="2026-07-02T10:00:00")

    assert result["primary"]["tx_id"] == "tx_primary"
    fakes.source.compile.assert_not_called()
    fakes.entity.update.assert_not_called()
    fakes.index.refresh.assert_not_called()
    fakes.log.append.assert_not_called()
    primary.run.assert_called_once()
```

- [x] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_knowledge_workflow.py::test_compile_primary_mode_runs_primary_without_legacy_compilers -q
```

Expected: fail because `KnowledgeWorkflowService` has no `primary_workflow` argument and still runs legacy compilers before mode-specific workflows.

- [x] **Step 3: Implement minimal primary routing**

Modify `KnowledgeWorkflowService.__init__` to accept `primary_workflow=None`. In `compile()`, after loading the item and timestamp but before source/entity/index/log compilers, resolve canonical mode. If mode is `primary`, call `self._primary.run(...)` and return `{"mode": mode, "errors": [], "primary": report}`. If primary raises, return legacy-shaped result with `errors=[{"stage": "primary", "error": str(e)}]` and do not run legacy compilers.

- [x] **Step 4: Run routing tests**

Run:

```bash
pytest tests/test_knowledge_workflow.py::test_compile_primary_mode_runs_primary_without_legacy_compilers tests/test_knowledge_workflow.py::test_compile_canary_mode_runs_after_legacy_workflow tests/test_knowledge_workflow.py::test_compile_shadow_mode_runs_after_legacy_workflow -q
```

Expected: pass.

- [x] **Step 5: Provide primary workflow dependency**

Create either `WikiPrimaryWorkflow` or configure `WikiCanaryWorkflow` with an `allow_all=True` constructor parameter. The primary workflow must use formal repository/projection and must not consult canary allowlist. It may reuse the canary review gate for high-risk actions.

- [x] **Step 6: Run canary/primary workflow tests**

Run:

```bash
pytest tests/test_wiki_canary_workflow.py tests/test_knowledge_workflow.py -q
```

Expected: pass.

## Task 3: WikiCompiler Compatibility Adapter

**Files:**
- Modify: `src/services/wiki_compiler.py`
- Modify: `tests/test_save_to_wiki_params.py` or add `tests/test_wiki_compiler_primary_adapter.py`

- [x] **Step 1: Write the failing adapter test**

Create `tests/test_wiki_compiler_primary_adapter.py`:

```python
from unittest.mock import MagicMock

from src.services.wiki_compiler import WikiCompiler
from src.utils.config import Config


def test_save_answer_primary_delegates_to_write_service(monkeypatch):
    Config.set("wiki.canonical_v2.mode", "primary")
    write_service = MagicMock()
    write_service.save.return_value = {
        "page_id": "page_primary",
        "sqlite_page_id": None,
        "canonical_saved": True,
        "fs_saved": False,
        "errors": [],
    }
    container = MagicMock()
    container.wiki_write_service = write_service
    monkeypatch.setattr("src.core.container.get_active_container", lambda: container)

    result = WikiCompiler().save_answer("Q", "A" * 120, ["k1"], auto_publish=False, enhance=False)

    assert result == "page_primary"
    write_service.save.assert_called_once()
```

- [x] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_wiki_compiler_primary_adapter.py -q
```

Expected: fail because `WikiCompiler.save_answer()` still writes SQLite directly in primary mode.

- [x] **Step 3: Implement adapter path**

At the top of `save_answer()`, if `wiki.canonical_v2.mode == "primary"`, fetch `get_active_container()`, call `container.wiki_write_service.save(...)`, emit a deprecation warning through `logger.warning`, and return `result["page_id"]`.

- [x] **Step 4: Run adapter and existing compiler tests**

Run:

```bash
pytest tests/test_wiki_compiler_primary_adapter.py tests/test_save_to_wiki_params.py -q
```

Expected: pass.

## Task 4: Guard Allowlist Shrink

**Files:**
- Modify: `tests/test_canonical_write_guards.py`
- Modify source files from Tasks 1-3 as needed

### Task 4A: WikiEntityUpdater Suggestion Service

**Files:**
- Modify: `src/services/wiki_entity_updater.py`
- Modify: `tests/test_wiki_entity_updater.py`
- Modify: `tests/test_wiki_frontmatter_source_ids.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write the failing no-write suggestion test**

Added `test_update_returns_suggestions_without_writing_pages` to assert `WikiEntityUpdater.update()` returns `suggestions` and does not create `wiki/entities` or `wiki/concepts` directories.

- [x] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_wiki_entity_updater.py::test_update_returns_suggestions_without_writing_pages -q
```

Observed: failed because the old updater incremented `entities_created` and wrote markdown files.

- [x] **Step 3: Replace direct write with suggestion builder**

Changed `WikiEntityUpdater` to keep LLM parsing and contradiction extraction, but return `_build_entity_suggestion(...)` dictionaries instead of calling `write_markdown(...)`.

- [x] **Step 4: Migrate old entity/frontmatter tests**

Updated entity updater tests to assert suggestions, suggestion body, and source ids instead of files.

- [x] **Step 5: Remove the guard allowlist entry**

Removed `("services/wiki_entity_updater.py", "write_markdown")` from `ALLOWED_DIRECT_WRITES` and removed `services/wiki_entity_updater.py` from `GUARDED`.

- [x] **Step 6: Verify**

Run:

```bash
pytest tests/test_canonical_write_guards.py tests/test_wiki_entity_updater.py tests/test_wiki_frontmatter_source_ids.py tests/test_knowledge_workflow.py -q
ruff check src tests evals tools scripts
mypy src tools
```

Observed: `27 passed`; ruff passed; mypy passed.

### Task 4B: KnowledgeWorkflow Save Query Draft Preparation

**Files:**
- Modify: `src/services/knowledge_workflow.py`
- Modify: `tests/test_knowledge_workflow.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write the failing no-write save_query test**

Added `test_save_query_prepares_draft_without_writing_markdown` to assert `save_query()` returns a draft payload and does not create the `wiki/syntheses` directory.

- [x] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_knowledge_workflow.py::test_save_query_prepares_draft_without_writing_markdown -q
```

Observed: failed because old behavior returned `status="saved"` and wrote markdown.

- [x] **Step 3: Remove direct markdown write**

Changed `KnowledgeWorkflowService.save_query()` to preserve threshold gating and draft metadata construction, but return `status="prepared"` with `frontmatter` and `body` instead of calling `write_markdown(...)`.

- [x] **Step 4: Migrate old save_query test**

Updated the old syntheses draft test to assert prepared payload fields and no filesystem write.

- [x] **Step 5: Remove guard allowlist entry**

Removed `("services/knowledge_workflow.py", "write_markdown")` from `ALLOWED_DIRECT_WRITES` and removed `services/knowledge_workflow.py` from `GUARDED`.

- [x] **Step 6: Verify**

Run:

```bash
pytest tests/test_canonical_write_guards.py tests/test_knowledge_workflow.py tests/test_wiki_write_service.py -q
ruff check src tests evals tools scripts
mypy src tools
```

Observed: `24 passed`; ruff passed; mypy passed.

### Task 4C: WikiSourceCompiler Source Summary Preparation

**Files:**
- Modify: `src/services/wiki_source_compiler.py`
- Modify: `tests/test_wiki_source_compiler.py`
- Modify: `tests/test_wiki_frontmatter_source_ids.py`
- Modify: `tests/test_knowledge_workflow.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write the failing no-write source summary test**

Added `test_compile_prepares_source_summary_without_writing_markdown` to assert `WikiSourceCompiler.compile()` returns a prepared payload and does not create `wiki/sources`.

- [x] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_wiki_source_compiler.py::test_compile_prepares_source_summary_without_writing_markdown -q
```

Observed: failed because the old compiler returned `status="compiled"` and wrote markdown files.

- [x] **Step 3: Remove direct markdown write**

Changed `WikiSourceCompiler` to build deterministic `frontmatter`, `body`, `slug`, and `suggested_path` fields without calling `write_markdown(...)` or reading `Config` filesystem paths.

- [x] **Step 4: Migrate source/frontmatter/workflow tests**

Updated source compiler and source_ids tests to assert prepared payload fields. Updated the path indexer e2e expectation so source summaries are no longer expected to appear as direct markdown files while index/log remain tracked as pending legacy writers.

- [x] **Step 5: Remove guard allowlist entry**

Removed `("services/wiki_source_compiler.py", "write_markdown")` from `ALLOWED_DIRECT_WRITES` and removed `services/wiki_source_compiler.py` from `GUARDED`.

- [x] **Step 6: Verify**

Run:

```bash
pytest tests/test_wiki_source_compiler.py tests/test_wiki_frontmatter_source_ids.py tests/test_knowledge_workflow.py tests/test_canonical_write_guards.py -q
```

Observed: `28 passed`.

### Task 4D: WikiIndexCompiler Index Preparation

**Files:**
- Modify: `src/services/wiki_index_compiler.py`
- Modify: `tests/test_wiki_index_compiler.py`
- Modify: `tests/test_knowledge_workflow.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write the failing no-write index test**

Added `test_refresh_prepares_index_without_writing_markdown` to assert `WikiIndexCompiler.refresh()` returns a prepared index payload and does not create `wiki/index.md`.

- [x] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_wiki_index_compiler.py::test_refresh_prepares_index_without_writing_markdown -q
```

Observed: failed because the old compiler returned `status="compiled"` and wrote markdown.

- [x] **Step 3: Remove direct markdown write**

Changed `WikiIndexCompiler` to scan existing wiki page directories and return `status`, `suggested_path`, `frontmatter`, `body`, and `page_count` without calling `write_markdown(...)` or creating the wiki directory.

- [x] **Step 4: Migrate index/workflow tests**

Updated index compiler tests to assert the returned body instead of reading `index.md`. Updated the path indexer e2e expectation so `index.md` is no longer expected to appear as a direct filesystem write while `log.md` remains tracked as a pending legacy writer.

- [x] **Step 5: Remove guard allowlist entry**

Removed `("services/wiki_index_compiler.py", "write_markdown")` from `ALLOWED_DIRECT_WRITES` and removed `services/wiki_index_compiler.py` from `GUARDED`.

- [x] **Step 6: Verify**

Run:

```bash
pytest tests/test_wiki_index_compiler.py tests/test_knowledge_workflow.py tests/test_canonical_write_guards.py -q
```

Observed: `25 passed`.

### Task 4E: WikiLogCompiler Log Preparation

**Files:**
- Modify: `src/services/wiki_log_compiler.py`
- Modify: `tests/test_wiki_log_compiler.py`
- Modify: `tests/test_knowledge_workflow.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write the failing no-write log tests**

Added `test_append_prepares_log_without_writing_markdown` and `test_rebuild_prepares_log_without_writing_markdown` to assert append/rebuild return prepared log bodies and do not create `wiki/log.md`.

- [x] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_wiki_log_compiler.py::test_append_prepares_log_without_writing_markdown tests/test_wiki_log_compiler.py::test_rebuild_prepares_log_without_writing_markdown -q
```

Observed: failed because the old compiler returned `status="appended"` / `status="rebuilt"` and wrote markdown.

- [x] **Step 3: Remove direct markdown writes**

Changed `WikiLogCompiler.append()` to return `status`, `suggested_path`, `event_hash`, and prepared `body` without using `Path.open("a")`. Changed `rebuild()` to return a sorted/deduplicated prepared `body` without using `write_text(...)`.

- [x] **Step 4: Migrate log/workflow tests**

Updated log tests to assert returned body content. Duplicate detection is now covered by seeding an existing `log.md` fixture before calling `append()` again. Updated the path indexer e2e expectation so `log.md` is no longer expected to appear as a direct filesystem write.

- [x] **Step 5: Remove guard allowlist entries**

Removed `("services/wiki_log_compiler.py", "write_text")` and `("services/wiki_log_compiler.py", "open_write")` from `ALLOWED_DIRECT_WRITES`; removed `services/wiki_log_compiler.py` from `GUARDED` and `OPEN_WRITE_GUARDED`.

- [x] **Step 6: Verify**

Run:

```bash
pytest tests/test_wiki_log_compiler.py tests/test_knowledge_workflow.py tests/test_canonical_write_guards.py -q
```

Observed: `26 passed`.

### Task 4F: API Wiki Routes Canonical Write Adapter

**Files:**
- Modify: `src/api/routes/wiki.py`
- Modify: `src/models/wiki_v2.py`
- Modify: `src/services/wiki_projection.py`
- Add: `tests/test_wiki_api_canonical_routes.py`
- Modify: `tests/test_wiki_projection.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write failing no-direct-DB route tests**

Added route-level tests for create/update/delete handlers with a fake DB that raises on `insert_wiki_page`, `update_wiki_page`, and `delete_wiki_page`. The tests assert the handlers save `WikiPage` objects through `wiki_repository` and process projection outbox.

- [x] **Step 2: Run the failing route tests**

Run:

```bash
pytest tests/test_wiki_api_canonical_routes.py -q
```

Observed: failed because the old handlers called `container.db.insert_wiki_page`, `container.db.update_wiki_page`, and `container.db.delete_wiki_page`.

- [x] **Step 3: Add projection compatibility red/green**

Added `test_project_page_updates_legacy_wiki_pages_compatibility_row` to prove canonical page projection also maintains the legacy `wiki_pages` read model. Added `test_disabled_projection_can_force_process_for_compatibility` so explicit write adapters can force projection while default disabled behavior still skips.

- [x] **Step 4: Route writes through WikiRepository**

Changed API create/update/delete handlers to build or load `WikiPage`, save through `wiki_repository.save_page(...)`, and call `wiki_projection.process_outbox(force=True)`. Added `PageStatus.DELETED` so API soft-delete keeps the legacy recycle-bin status through projection.

- [x] **Step 5: Remove guard allowlist entries**

Removed `api/routes/wiki.py` entries for `insert_wiki_page`, `update_wiki_page`, and `delete_wiki_page` from `ALLOWED_DIRECT_WRITES`, and removed the module from `GUARDED`.

- [x] **Step 6: Verify**

Run:

```bash
pytest tests/test_wiki_api_canonical_routes.py tests/test_api.py::TestPhase5WebContracts::test_wiki_create_update_and_workflow_contract tests/test_wiki_projection.py tests/test_canonical_write_guards.py -q
```

Observed: `23 passed`.

### Task 4G: WikiWorkflow Canonical Status Transitions

**Files:**
- Modify: `src/services/wiki_workflow.py`
- Modify: `src/services/wiki_projection.py`
- Add: `tests/test_wiki_workflow_canonical.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write failing no-direct-DB workflow tests**

Added tests for `submit_for_review()` and `restore_version()` using a fake `Database` that raises on `update_wiki_page`. The tests assert workflow status/version changes save canonical `WikiPage` objects through `WikiRepository`, process projection, and keep workflow history logging.

- [x] **Step 2: Run the failing workflow tests**

Run:

```bash
pytest tests/test_wiki_workflow_canonical.py -q
```

Observed: failed because the old workflow called `Database.update_wiki_page(...)` for both status transitions and version restore.

- [x] **Step 3: Route workflow writes through canonical services**

Changed `WikiWorkflow` to resolve active container repository/projection, or construct them from `Config` when no container is active. Status transitions and version restore now save canonical pages and force projection; workflow history and version snapshots remain in the legacy operation tables.

- [x] **Step 4: Preserve restore compatibility fields**

Added `WikiProjection.update_legacy_page_fields(...)` for compatibility-only read-model fields such as `concept_summary`, and made legacy projection preserve existing `concept_summary`, `lint_score`, and `complex_anomaly` when canonical pages are reprojected.

- [x] **Step 5: Remove guard allowlist entry**

Removed `("services/wiki_workflow.py", "update_wiki_page")` from `ALLOWED_DIRECT_WRITES` and removed `services/wiki_workflow.py` from `GUARDED`.

- [x] **Step 6: Verify**

Run:

```bash
pytest tests/test_wiki_workflow_canonical.py tests/test_api.py::TestPhase5WebContracts::test_wiki_create_update_and_workflow_contract tests/test_canonical_write_guards.py tests/test_wiki_projection.py -q
```

Observed: `22 passed`.

### Task 4H: WikiLint Canonical Compatibility Updates

**Files:**
- Modify: `src/services/wiki_lint.py`
- Modify: `src/services/wiki_workflow.py`
- Add: `tests/test_wiki_lint_canonical.py`
- Modify: `tests/test_wiki_workflow_canonical.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write failing no-direct-write lint tests**

Added tests with a fake database that raises on `update_wiki_page`. They cover lint-score projection, externally callable complex-anomaly marking, and duplicate-page deprecation through canonical save.

- [x] **Step 2: Run the failing lint tests**

Run:

```bash
pytest tests/test_wiki_lint_canonical.py -q
```

Observed: `3 failed`; the old implementation called `Database.update_wiki_page(...)` for all three paths.

- [x] **Step 3: Route lint writes through canonical services**

Compatibility-only fields (`lint_score`, `complex_anomaly`, `concept_summary`) now use `WikiProjection.update_legacy_page_fields(...)`. Canonical content, source IDs, and status changes delegate to `WikiWorkflow._save_canonical_page(...)`, which saves through `WikiRepository` and forces projection. Added `source_ids` support to that shared canonical-save helper.

- [x] **Step 4: Verify source ID propagation**

Added a workflow regression test that first failed, then passed after `source_ids` was applied to the canonical `WikiPage`.

- [x] **Step 5: Remove guard allowlist entry**

Removed `("services/wiki_lint.py", "update_wiki_page")` from `ALLOWED_DIRECT_WRITES` and removed `services/wiki_lint.py` from `GUARDED`.

- [x] **Step 6: Verify**

Run:

```bash
pytest tests/test_canonical_write_guards.py tests/test_wiki_lint.py tests/test_wiki_lint_canonical.py tests/test_wiki_workflow_canonical.py tests/test_wiki_projection.py -q
ruff check src/services/wiki_lint.py src/services/wiki_workflow.py tests/test_wiki_lint.py tests/test_wiki_lint_canonical.py tests/test_wiki_workflow_canonical.py tests/test_canonical_write_guards.py
mypy src/services/wiki_lint.py src/services/wiki_workflow.py
```

Observed: `34 passed`; Ruff passed; mypy reported no issues.

- [x] **Step 7: Review injected-service boundaries**

Independent review found that the initial fallback ignored an explicitly injected lint database, and that a custom repository without an explicit projection could become detached from the legacy read model. `WikiLint` now accepts optional repository/projection dependencies and constructs a `WikiProjection` with the same injected database whenever needed. A follow-up review also removed default-service construction when both services are injected, preventing an unintended workspace `wiki/` directory. Real temporary SQLite tests verify canonical duplicate deprecation and content/source/status projection back to `wiki_pages`.

- [x] **Step 1: Remove resolved allowlist entries**

All entries are now removed. The primary write entrypoints and compatibility adapters no longer call
`Database.insert_wiki_page(...)`, `Database.update_wiki_page(...)`, or
`Database.delete_wiki_page(...)` directly. `ALLOWED_DIRECT_WRITES`, `GUARDED`, and
`OPEN_WRITE_GUARDED` are empty; the guard test remains as a future regression tripwire.

- [x] **Step 2: Run guard tests**

Run:

```bash
pytest tests/test_canonical_write_guards.py -q
```

Observed in the current targeted regression set: `32 passed` (including canonical write guards).

### Task 4I: WikiCompiler Canonical Compatibility Adapter

**Files:**
- Modify: `src/services/wiki_compiler.py`
- Modify: `src/services/wiki_workflow.py`
- Modify: `src/services/wiki_projection.py`
- Add: `tests/test_wiki_compiler_canonical.py`
- Modify: `tests/test_wiki_workflow_canonical.py`
- Modify: `tests/test_canonical_write_guards.py`

- [x] **Step 1: Write failing no-direct-Database tests**

Added `tests/test_wiki_compiler_canonical.py`. A fake database raises on legacy insert/update
calls while tests exercise query save, stale source cleanup, new-page creation, and existing-page
update paths.

- [x] **Step 2: Route all compiler page writes through the canonical save helper**

`WikiCompiler` now delegates legacy page persistence to `WikiWorkflow._save_canonical_page()`.
The helper builds/saves a canonical `WikiPage`, processes Projection, and preserves exact legacy
content after Markdown serialization.

- [x] **Step 3: Preserve compatibility projection fields**

`WikiProjection.update_legacy_page_fields(...)` now accepts `content` alongside the existing
compatibility-only fields. This avoids a trailing-newline regression for legacy callers while the
Canonical store remains the source of truth.

- [x] **Step 4: Verify focused regression set**

Run:

```bash
pytest tests/test_canonical_write_guards.py tests/test_wiki_compiler_canonical.py tests/test_save_to_wiki_params.py tests/test_wiki_compiler_primary_adapter.py tests/test_wiki_workflow_canonical.py tests/test_wiki_projection.py -q
```

Observed: `32 passed`.

## Task 5: Phase 4C Review and Commit

**Files:**
- Modify: `PROGRESS.md`
- Create: `docs/superpowers/reviews/2026-07-09-phase4c-primary-review.md`
- Optional artifact: `artifacts/eval/wiki-v2-phase4c-primary-<sample>.json`

- [x] **Step 1: Run phase verification**

Run:

```bash
pytest tests/test_wiki_write_service.py tests/test_knowledge_workflow.py tests/test_wiki_compiler_primary_adapter.py tests/test_canonical_write_guards.py -q
ruff check src tests evals tools scripts
mypy src tools
python evals/run_retrieval_eval.py --all
python evals/run_wiki_eval.py
```

Observed on final Phase 4C HEAD: Ruff passed; mypy reported no issues in 189 source files;
retrieval eval reported `Overall: PASS`; wiki eval completed with source coverage `0.0`, cross-page
update rate `0.9545`, orphan rate `0.0`, query-save rate `0.0606`, and stale ratio `0.0`.

- [x] **Step 2: Run full test suite**

Run:

```bash
pytest -q
```

Observed: `1455 passed / 2 skipped / 5 xfailed / 8 warnings`.

- [x] **Step 3: Update progress and review**

Update `PROGRESS.md` Phase 4C status only if the direct-write guard shrink and primary-mode write path are verified. Create the review document with:

- Changed entrypoints.
- Removed allowlist entries.
- Compatibility fields preserved.
- Verification command outputs.
- Residual risks for Phase 5.

- [x] **Step 4: Commit**

Run:

```bash
git add PROGRESS.md docs/superpowers/reviews/2026-07-09-phase4c-primary-review.md src tests
git commit -m "refactor(wiki-v2): switch primary canonical write path"
```

Completed by the Phase 4C acceptance commit after gitleaks scan.

## Self-Review

Spec coverage:

- `KnowledgeWorkflowService` primary orchestration is covered by Task 2.
- `WikiWriteService` compatibility fields and no double-write primary behavior are covered by Task 1.
- `WikiCompiler` compatibility adapter is covered by Task 3.
- Guard allowlist shrink is covered by Task 4.
- Review and commit are covered by Task 5.

Known gap:

- Full conversion of `WikiEntityUpdater`, `WikiSourceCompiler`, `WikiIndexCompiler`, and `WikiLogCompiler` direct file writes may require additional Phase 4C tasks after Task 2 proves primary mode bypasses legacy compilers. Do not mark Phase 4C complete while any direct write allowlist entry remains that is required to be removed by the Phase 4C acceptance gate.

Placeholder scan:

- No `TBD`, `TODO`, or unspecified test commands remain.

Type consistency:

- `page_id`, `canonical_saved`, `projection_pending`, and `projection_processed` are used consistently between test and implementation steps.
