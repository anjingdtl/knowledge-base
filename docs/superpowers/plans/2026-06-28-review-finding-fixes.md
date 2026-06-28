# Review Finding Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three runtime defects found in the review of the latest `master` update: missing pair rejudge API, unsafe version-conflict delete precondition, and broken `auto_tag` per-item error handling.

**Architecture:** Keep the fixes narrowly scoped to existing maintenance, version-conflict, and MCP auto-tag paths. Add regression tests first, then make minimal service/API changes so the UI and destructive backend paths share validated behavior.

**Tech Stack:** Python 3, FastAPI, pytest, SQLite, React/TypeScript, Vite.

---

## Scope

In scope:
- Add a real `POST /api/maintenance/version-conflict/pairs/{pair_id}/judge` backend route for the existing Maintenance UI button.
- Add service-level validation so `execute_delete()` rejects a `newer_item_id` that is not one of the two pair item IDs.
- Fix MCP `auto_tag()` error collection so a bad LLM response skips only the current row instead of failing the whole batch.
- Add targeted regression tests for all three defects.

Out of scope:
- Redesigning the Maintenance UI.
- Changing scan heuristics, LLM prompts, or embedding thresholds.
- Cleaning the pre-existing untracked local scripts/reports.
- Committing or pushing unless requested separately.

## Files

- Modify: `src/services/version_conflict.py`
  - Add `judge_pair(pair_id, run_synchronously=True)` for pair-level rejudge.
  - Validate `newer_item_id` before choosing the old item to delete.
- Modify: `src/api/routes/maintenance.py`
  - Add `POST /version-conflict/pairs/{pair_id}/judge`.
- Modify: `src/mcp_server.py`
  - Replace `row.get(...)` in `auto_tag()` exception handling with safe row access.
- Modify: `tests/test_version_conflict.py`
  - Add service tests for invalid `newer_item_id` and pair-level rejudge.
- Modify: `tests/test_maintenance_api.py`
  - Add API route regression for pair-level rejudge not found behavior.
- Modify: `tests/test_critical_bugfix_e2e.py`
  - Add regression for malformed LLM tag output skipping one row without failing the whole tool call.
- Validate: `client`
  - Run `npm run build` to ensure the existing UI call remains type/build clean.

---

### Task 1: Pair-Level Rejudge API

**Files:**
- Modify: `src/services/version_conflict.py`
- Modify: `src/api/routes/maintenance.py`
- Test: `tests/test_version_conflict.py`
- Test: `tests/test_maintenance_api.py`

- [ ] **Step 1: Write failing service test**

Add a test proving a single pair can be rejudged without depending on session-level batch judgment:

```python
def test_judge_pair_rejudges_single_pair(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    pairs = svc._repo.list_pairs(session_id, status="pending")
    target = pairs[0]

    result = svc.judge_pair(target["id"], run_synchronously=True)

    assert result["ok"] is True
    assert result["judged"] == 1
    updated = svc._repo.get_pair(target["id"])
    assert updated.judged_at is not None
    assert updated.relation_type in ("supersedes", "superseded_by", "partial_overlap", "unrelated")
```

- [ ] **Step 2: Write failing API test**

Add a route-level regression for not-found behavior:

```python
def test_judge_pair_not_found(self, api_client):
    resp = api_client.post(
        "/api/maintenance/version-conflict/pairs/nonexistent/judge",
        json={},
    )
    assert resp.status_code == 404
```

- [ ] **Step 3: Run red tests**

Run:

```powershell
pytest tests/test_version_conflict.py::test_judge_pair_rejudges_single_pair tests/test_maintenance_api.py::TestMaintenanceAPI::test_judge_pair_not_found -q
```

Expected: fail because `VersionConflictService.judge_pair` and the API route do not exist.

- [ ] **Step 4: Implement minimal service and route**

Add a private helper that judges a provided pair list, reuse it from `judge_pending_pairs()`, and expose `judge_pair()` plus the FastAPI route. The pair route should return 404 when the pair does not exist and otherwise return the service result.

- [ ] **Step 5: Run green tests**

Run:

```powershell
pytest tests/test_version_conflict.py::test_judge_pair_rejudges_single_pair tests/test_maintenance_api.py::TestMaintenanceAPI::test_judge_pair_not_found -q
```

Expected: pass.

---

### Task 2: Destructive Delete Precondition

**Files:**
- Modify: `src/services/version_conflict.py`
- Test: `tests/test_version_conflict.py`

- [ ] **Step 1: Write failing test**

Add a regression proving invalid `newer_item_id` values are rejected and neither side is deleted:

```python
def test_execute_delete_rejects_newer_item_id_outside_pair(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    pair = ConflictPair(
        session_id=session_id,
        item_a_id=policies["old"].id,
        item_b_id=policies["new"].id,
        candidate_source="sql_tag",
        relation_type="supersedes",
        newer_item_id="not-a-member",
        confidence=0.9,
        reason="invalid newer id",
        status="pending",
    )
    svc._repo.create_pair(pair)

    result = svc.execute_delete(pair.id)

    assert result["ok"] is False
    assert result["error"]["code"] == "PRECONDITION_FAILED"
    assert "newer_item_id" in result["error"]["message"]
    kr = svc._get_knowledge_repo()
    assert kr.get(policies["old"].id) is not None
    assert kr.get(policies["new"].id) is not None
```

- [ ] **Step 2: Run red test**

Run:

```powershell
pytest tests/test_version_conflict.py::test_execute_delete_rejects_newer_item_id_outside_pair -q
```

Expected: fail because the current implementation deletes `item_a_id`.

- [ ] **Step 3: Implement validation**

Before choosing `deleted_id`, reject `pair.newer_item_id` unless it equals `pair.item_a_id` or `pair.item_b_id`.

- [ ] **Step 4: Run green test**

Run:

```powershell
pytest tests/test_version_conflict.py::test_execute_delete_rejects_newer_item_id_outside_pair -q
```

Expected: pass.

---

### Task 3: Auto-Tag Error Handling

**Files:**
- Modify: `src/mcp_server.py`
- Test: `tests/test_critical_bugfix_e2e.py`

- [ ] **Step 1: Write failing test**

Add a regression proving malformed LLM output records one row error instead of failing the whole tool:

```python
def test_auto_tag_bad_llm_json_skips_row_without_internal_error(self):
    import src.mcp_server as mcp_mod
    from tests.conftest import insert_test_knowledge

    kid = insert_test_knowledge("坏响应文档", "内容", tags=None)
    mock_llm = MagicMock()
    mock_llm.chat_with_usage.return_value = ("不是 JSON", {})

    original_get, original_check = self._patch_container(mock_llm)
    try:
        result = mcp_mod.auto_tag(limit=1)
    finally:
        self._restore(original_get, original_check)

    assert result["ok"] is True
    assert result["data"]["tagged_count"] == 0
    assert result["data"]["skipped_count"] == 1
    assert result["meta"]["error_count"] == 1
    assert _get_tags(kid) == []
```

- [ ] **Step 2: Run red test**

Run:

```powershell
pytest tests/test_critical_bugfix_e2e.py::TestAutoTagRealDb::test_auto_tag_bad_llm_json_skips_row_without_internal_error -q
```

Expected: fail because `sqlite3.Row` has no `.get()` in the exception block.

- [ ] **Step 3: Implement safe row access**

Replace `row.get(...)` with a small local helper using `row.keys()` and `row[key]`, falling back to `"?"` on access errors.

- [ ] **Step 4: Run green test**

Run:

```powershell
pytest tests/test_critical_bugfix_e2e.py::TestAutoTagRealDb::test_auto_tag_bad_llm_json_skips_row_without_internal_error -q
```

Expected: pass.

---

### Task 4: Final Verification Gate

**Files:**
- Validate only; no production edits expected.

- [ ] **Step 1: Run targeted backend regression suite**

Run:

```powershell
pytest tests/test_version_conflict.py tests/test_maintenance_api.py tests/test_critical_bugfix_e2e.py tests/test_50round_bugfix.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend production build**

Run:

```powershell
npm run build
```

Working directory: `client`

Expected: TypeScript and Vite build pass.

- [ ] **Step 3: Check final working tree**

Run:

```powershell
git status --short --branch
```

Expected: only intended modified files plus pre-existing untracked local files.

---

## Final Acceptance Criteria

- The Maintenance UI's rejudge button maps to a real backend route.
- Invalid version-conflict `newer_item_id` values cannot cause accidental deletion.
- MCP `auto_tag()` tolerates malformed LLM output on a single row and returns structured per-row errors.
- Targeted backend regression suite passes.
- Frontend build passes.
