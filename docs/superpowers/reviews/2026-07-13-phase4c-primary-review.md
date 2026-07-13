# Canonical Wiki V2 Phase 4C Primary Review

> Review date: 2026-07-13
> Branch: `feature/wiki-v2-phase4a-shadow`
> Plan: `docs/superpowers/plans/2026-07-09-canonical-wiki-v2-phase4c-primary-plan.md`

## Scope and Result

Phase 4C makes Canonical Wiki V2 the primary write path. The final review covers primary ingest,
query save, compatibility adapters, API/workflow/lint writes, Projection compatibility, and the
direct-write guard.

The phase is accepted. No unresolved blocking issue remains after the final review fixes below.

## Delivered Entry Points

| Entry point | Result |
|---|---|
| `KnowledgeWorkflowService` | `primary` invokes `WikiPrimaryWorkflow` before legacy compilers. |
| `WikiWriteService` | Query save creates a canonical draft page through Repository and Projection. |
| `WikiCompiler` | Legacy save/update/repair entry points delegate to `WikiWorkflow._save_canonical_page()`. |
| API / workflow / lint | Page CRUD, status/restore and lint repair use Repository and/or Projection rather than legacy page writes. |
| Legacy compilers | entity/source/index/log/query paths emit suggestions or prepared payloads rather than direct markdown writes. |

## Review Findings Closed

1. The first Phase 4C guard cleanup also cleared `GUARDED`, so it no longer scanned migrated
   modules. The final fix restores coverage for nine entrypoints and the log open-write detector,
   while keeping `ALLOWED_DIRECT_WRITES` empty. `test_primary_write_guard_keeps_scanning_migrated_entrypoints`
   locks this behavior.
2. The compiler's default service path can use the active DI container. The test fixture now resets
   that module-level container and sets a per-test `knowledge_workflow.wiki_dir`, preventing one
   test's container from writing to another test's database or the repository root.
3. A V2-only SQLite Projection can omit legacy `wiki_pages`; compatibility projection now becomes a
   no-op in that case while V2 projection remains functional. When the legacy table exists, content,
   source IDs, status and compatibility fields remain projected.
4. Ten generated `wiki/` runtime files were accidentally included in `d6ccf02`. They are removed
   from version control and `/wiki/` is ignored so test/runtime output cannot re-enter the branch.

## Compatibility

- `sqlite_page_id` remains a deprecated compatibility result; canonical `page_id` is present for
  primary writes.
- Legacy `wiki_pages` stays a read-model projection. Canonical content serialization may add a
  Markdown trailing newline, so legacy callers receive their exact supplied content through the
  compatibility patch.
- Projection remains rebuildable from Canonical Repository state; missing optional legacy tables do
  not block V2-only consumers.

## Verification

```text
pytest tests/test_core.py::TestDIContainer::test_create_container tests/test_save_to_wiki_params.py -q
6 passed

pytest tests/test_save_to_wiki_params.py tests/test_wiki_canonical_mode.py \
       tests/test_wiki_lint_canonical.py tests/test_wiki_query_service.py \
       tests/test_wiki_v2_transaction_recovery.py -q
47 passed

pytest tests/test_canonical_write_guards.py -q
6 passed

pytest -q
1455 passed, 2 skipped, 5 xfailed, 8 warnings

ruff check src tests evals tools scripts
All checks passed

mypy src tools
Success: no issues found in 189 source files

python evals/run_retrieval_eval.py --all
Overall: PASS

python evals/run_wiki_eval.py
source_coverage: 0.0
cross_page_update_rate: 0.9545
orphan_page_rate: 0.0
query_save_rate: 0.0606
stale_claim_ratio: 0.0
```

## Residual Risks for Phase 5

- Five C2 matcher cases remain xfailed: unit, model, region, negation and intensity distinctions
  must continue to resolve conservatively.
- Interrupted publish can still leave claim-directory orphans; Phase 5 dependency/rebuild work
  should explicitly converge that edge.
- Phase 5 must start with dependency impact planning, bounded traversal and cancel/max-page guards;
  do not widen automatic publishing.
