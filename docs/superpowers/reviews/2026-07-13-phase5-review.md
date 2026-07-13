# Canonical Wiki V2 Phase 5 Dependency Invalidation Review

> Review date: 2026-07-13
> Branch: `feature/wiki-v2-phase5-dependency-invalidation`
> Spec: `docs/superpowers/specs/2026-07-13-wiki-v2-phase5-dependency-invalidation-design.md`
> Plan: `docs/superpowers/plans/2026-07-13-wiki-v2-phase5-dependency-invalidation.md`

## Scope and Result

Phase 5 implements source update/delete → precise evidence invalidation → conservative
claim/page transition → staged rebuild via `WikiRepository` transactions → projection refresh.

The phase is accepted. Source evolution no longer over-invalidates: unchanged blocks are
retained (u01/u03), changed blocks mark evidence stale (u02), source delete keeps claims with
remaining support active (E2E-4/d01) and only transitions orphaned claims to `unsupported`
(E2E-3/d02) without physical deletion (d03).

## Delivered

| Task | Commit | Deliverable |
|---|---|---|
| T5.0 | `5ab04c0` | `Evidence.stale`/`stale_at` field + projection column + alembic j002 |
| T5.1 | `4f76d12` | `WikiDependencyService` (impact graph + `get_impacted_by_source/claim` + cycle detection + max_depth) |
| T5.2a | `204e80c` | `WikiRebuildService.plan_rebuild` dry-run + shared `compute_excerpt_hash` |
| T5.2b | `804e35d` | `WikiRebuildService.rebuild` (staging tx + projection refresh + cooperative cancel + max_pages) |
| T5.2c | `813cb9c` | `wiki_dependencies` table projection (rebuildable read model) |
| T5.3a | `0b12b0c` | `RebuildScheduler` per-kid debounce |
| T5.3b | `4e48f77` | Trigger wiring: workflow gate, path_indexer delete hook, CLI `shinehe rebuild` |
| T5.4 | (this) | C2 source golden set enabled + E2E-3/E2E-4 + full gate |

## Key Implementation Decisions

1. **Dependency graph is on-demand from canonical state**, not the projection table.
   `WikiDependencyService` traverses `list_claims`/`list_pages`; `wiki_dependencies` is a
   rebuildable read model populated by `WikiProjection`. Planner never depends on the table
   being filled (iron rule: projection is not a second knowledge source).

2. **`Evidence.stale` is in-model** (not a sidecar registry). Stale evidence is retained for
   audit (d03); claims move to `unsupported` but are never retracted. `active` judgment looks
   only at `supports` evidence, matching `Claim.validate()` invariant.

3. **Block-diff reuses ingest-time `excerpt_hash`** (`sha256:` prefix). `compute_excerpt_hash`
   was extracted to `wiki_claim_extractor` and canary/primary/shadow now delegate to it, so
   rebuild compares identical hashes (no false stale from algorithm mismatch).

4. **Cancel raises `_RebuildCancelled`** inside the transaction so `WikiRepository.transaction`
   rolls back staged partial work (consistent state), rather than auto-committing on `return`.

5. **`rebuild.auto_allowlist` is decoupled from `canonical_v2.canary`** — independent
   knowledge_ids/source_paths, default empty = pure manual. `auto_on_source_update=false`
   by default; canary level = allowlist hits only.

6. **Source delete hook** (`try_schedule_source_delete`) is non-blocking and gated; defaults
   off; failures never break the delete main flow.

## Iron Rule Adherence

- All canonical writes via `WikiRepository.transaction()` (no direct file/YAML/table writes).
- `ALLOWED_DIRECT_WRITES` stays empty; C6 guard extended to scan the 3 new service files.
- 3 new services use constructor DI; no `Config`/`Database`/`get_active_container` imports.
- C2's 5 xfail (unit/model/region/negation/intensity) preserved unchanged.
- No legacy fallback removed; no auto-publish widened; no Phase 6 work (migration/feedback).

## Verification

```text
pytest -q
1497 passed, 2 skipped, 5 xfailed, 8 warnings in 356.07s
  (Phase 4C baseline 1455 passed; Phase 5 adds 42 tests; 5 xfail preserved)

ruff check src tests evals tools scripts
All checks passed

mypy src
Success: no issues found in 189 source files

python evals/run_retrieval_eval.py --all
Overall: PASS   (retrieval_zh Recall@5 0.6000, MRR 0.3400, nDCG@10 0.4036)

python evals/run_wiki_eval.py
source_coverage: 0.0
cross_page_update_rate: 0.9545
orphan_page_rate: 0.0
query_save_rate: 0.0152   (data-driven metric; Phase 5 does not touch query-save logic)
stale_claim_ratio: 0.0
```

E2E-3/E2E-4 integration tests (`tests/test_wiki_v2_phase5_e2e.py`) pass against a real
`WikiRepository`. C2 `source_update`/`source_delete` golden sets are now consumed
(`tests/test_wiki_v2_golden_eval.py`, u01-u03 + d01-d03).

## Residual Risks for Phase 6

- `_clear_v2_tables` orphan: `source→evidence` edges may linger when evidence is removed
  incrementally; `rebuild()` full re-population repairs this (documented in projection code).
- Auto rebuild is off by default; real-data canary of `auto_allowlist`/`auto_on_source_update`
  should happen before widening (deferred to Phase 6 migration cutover, per correction plan §6).
- Transaction recovery for rebuild reuses C3's `recover()`; a dedicated rebuild-stage fault
  injection was not added (rebuild uses the same `WikiTransaction`, already covered by C3 tests).
