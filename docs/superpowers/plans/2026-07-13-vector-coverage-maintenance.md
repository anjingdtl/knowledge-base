# Vector Coverage Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Maintenance Center action that safely embeds only blocks missing vectors and shows the repair outcome.

**Architecture:** `src.services.indexer` will expose coverage and targeted-repair functions. `MaintenanceView` will call the repair from a Qt worker and update only on Qt signals.

**Tech Stack:** Python, SQLite/sqlite-vec, PySide6, pytest.

---

### Task 1: Targeted vector-repair service

**Files:**
- Modify: `src/services/indexer.py:297-547`
- Modify: `tests/test_indexer.py:1-260`

- [ ] **Step 1: Write failing service tests**

Add tests that insert two ordinary `blocks` rows, write a vector for one via `BlockStore.add_block_embedding`, then call `repair_missing_block_vectors(batch_size=1)` with a mocked `EmbeddingService`. Assert the mock sees only the missing block's result of `build_embedding_text`, and assert:

~~~python
assert result == {
    "total_blocks": 2,
    "missing_before": 1,
    "repaired": 1,
    "failed": 0,
    "coverage_before": 0.5,
    "coverage_after": 1.0,
    "errors": [],
}
assert get_vector_coverage()["covered_blocks"] == 2
~~~

Add a second test whose mocked `embed_batch_with_cache` raises `RuntimeError("embedding unavailable")` for the first one-block batch and returns `[[0.3] * 1024]` for the second. Assert it returns `repaired == 1`, `failed == 1`, `coverage_after == 0.5`, and:

~~~python
assert result["errors"] == [{
    "block_ids": ["failing-vector"],
    "error": "embedding unavailable",
}]
~~~

Add a third test that clears `embedding.model`, `embedding.api_key`, and `llm.api_key`; assert `repair_missing_block_vectors()` raises `ValueError` matching `Embedding 模型未配置`.

- [ ] **Step 2: Run the service tests and verify they fail**

Run: `pytest tests/test_indexer.py -k "repair_missing_block_vectors" -v`

Expected: collection fails because `get_vector_coverage` and `repair_missing_block_vectors` are not yet importable.

- [ ] **Step 3: Implement the smallest repair API**

Add these functions before `reindex_all` in `src/services/indexer.py`. Call `BlockStore()._ensure_table()`, and count vectors with the same `blocks.rowid = vec_blocks.rowid` relationship used by `kb_health_check`.

~~~python
def get_vector_coverage() -> dict[str, int | float]:
    row = Database.get_conn().execute(
        "SELECT COUNT(*) AS total_blocks, "
        "SUM(CASE WHEN v.rowid IS NOT NULL THEN 1 ELSE 0 END) AS covered_blocks "
        "FROM blocks AS b LEFT JOIN vec_blocks AS v ON v.rowid = b.rowid"
    ).fetchone()
    total = int(row["total_blocks"] or 0)
    covered = int(row["covered_blocks"] or 0)
    return {
        "total_blocks": total,
        "covered_blocks": covered,
        "missing_blocks": total - covered,
        "coverage": covered / total if total else 1.0,
    }
~~~

Implement `repair_missing_block_vectors(progress_callback=None, batch_size=None)` to:

~~~python
if not Config.get("embedding.model", ""):
    raise ValueError("Embedding 模型未配置，请先在设置中配置 Embedding 模型")
if not (Config.get("embedding.api_key", "") or Config.get("llm.api_key", "")):
    raise ValueError("Embedding API Key 未配置，请先在设置中配置 API Key")
rows = [dict(row) for row in Database.get_conn().execute(
    "SELECT b.* FROM blocks AS b "
    "LEFT JOIN vec_blocks AS v ON v.rowid = b.rowid "
    "WHERE v.rowid IS NULL ORDER BY b.rowid"
).fetchall()]
~~~

For every bounded batch, build text with `EmbeddingService.build_embedding_text(row)`, call `embed_batch_with_cache(texts, batch_size=len(batch))`, reject a vector-count mismatch, and persist successful batches with `BlockStore.add_block_embeddings_batch(block_ids, vectors)`. Catch exceptions per batch, append `{"block_ids": block_ids, "error": str(exc)}`, and still invoke `progress_callback(processed, total)`. Return the exact fields asserted in Step 1, with coverage sampled before and after.

- [ ] **Step 4: Run the service tests and verify they pass**

Run: `pytest tests/test_indexer.py -k "repair_missing_block_vectors" -v`

Expected: 3 passed.

- [ ] **Step 5: Commit the service repair**

~~~bash
git add src/services/indexer.py tests/test_indexer.py
git commit -m "feat: repair missing block vectors"
~~~

### Task 2: Maintenance Center worker and controls

**Files:**
- Modify: `src/gui/maintenance_view.py:1-115,321-679`
- Create: `tests/test_maintenance_view.py`

- [ ] **Step 1: Write failing GUI-worker tests**

Create an offscreen `QApplication` fixture following `tests/test_settings_dialog_mcp_profile.py`. Add a test which imports `VectorCoverageRepairWorker`, monkeypatches `src.gui.maintenance_view.repair_missing_block_vectors` with:

~~~python
def fake_repair(progress_callback):
    progress_callback(2, 3)
    return {
        "total_blocks": 3, "missing_before": 3, "repaired": 3,
        "failed": 0, "coverage_before": 0.0, "coverage_after": 1.0,
        "errors": [],
    }
~~~

Connect both worker signals, call `worker.run()`, and assert progress equals `[(2, 3)]` and the result contains `coverage_after == 1.0`.

Add a view test which monkeypatches `get_vector_coverage` to return `{"total_blocks": 12, "covered_blocks": 9, "missing_blocks": 3, "coverage": 0.75}`, constructs `MaintenanceView`, and asserts:

~~~python
assert view.lbl_vector_coverage.text() == "向量覆盖率: 75.0% (9/12)"
assert view.btn_repair_vectors.text() == "修复向量覆盖率"
~~~

- [ ] **Step 2: Run the GUI-worker tests and verify they fail**

Run: `pytest tests/test_maintenance_view.py -v`

Expected: FAIL because `VectorCoverageRepairWorker`, `lbl_vector_coverage`, and `btn_repair_vectors` do not exist.

- [ ] **Step 3: Implement worker and UI behavior**

Import `get_vector_coverage` and `repair_missing_block_vectors` in `maintenance_view.py`. Add a `VectorCoverageRepairWorker(QThread)` beside the existing workers:

~~~python
class VectorCoverageRepairWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(dict)
    error = Signal(str)

    def run(self) -> None:
        try:
            self.finished_ok.emit(repair_missing_block_vectors(self.progress.emit))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
~~~

In `MaintenanceView`, retain the worker in `self._vector_repair_worker`; put `lbl_vector_coverage` and accent-styled `btn_repair_vectors` in the header; and call `_refresh_vector_coverage` after building the UI and whenever the view is shown.

Implement:

- `_refresh_vector_coverage`: display `向量覆盖率: {coverage:.1%} ({covered_blocks}/{total_blocks})`.
- `_on_repair_vectors`: confirm that the configured Embedding model will be called and may incur API usage; if confirmed, disable the button, show `向量覆盖率: 修复中... (0/{missing_blocks})`, wire the worker signals, and start it.
- `_on_vector_repair_progress`: display `向量覆盖率: 修复中... ({current}/{total})`.
- `_on_vector_repair_finished`: clear the worker, re-enable the button, refresh the coverage, and show pre/post coverage plus repaired and failed counts; include the first batch error only if failures occurred.
- `_on_worker_error`: additionally clear the vector worker, re-enable the repair button, and refresh coverage before using the existing warning dialog.

- [ ] **Step 4: Run the GUI-worker tests and verify they pass**

Run: `pytest tests/test_maintenance_view.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit the GUI action**

~~~bash
git add src/gui/maintenance_view.py tests/test_maintenance_view.py
git commit -m "feat: add vector coverage maintenance action"
~~~

### Task 3: Focused regression verification

**Files:**
- Verify: `src/services/indexer.py`
- Verify: `src/gui/maintenance_view.py`
- Verify: `tests/test_indexer.py`
- Verify: `tests/test_maintenance_view.py`

- [ ] **Step 1: Run focused regressions**

Run: `pytest tests/test_indexer.py tests/test_maintenance_view.py tests/test_v160_stability_report_fixes.py -v`

Expected: all selected tests pass, including the v1.6.0 stale-checkpoint regression.

- [ ] **Step 2: Run static checks**

Run: `ruff check src/services/indexer.py src/gui/maintenance_view.py tests/test_indexer.py tests/test_maintenance_view.py`

Expected: `All checks passed!`

- [ ] **Step 3: Inspect the final change set**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and no uncommitted production or test changes.

