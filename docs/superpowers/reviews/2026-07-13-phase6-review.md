# Canonical Wiki V2 Phase 6 Migration / Feedback / Eval Review

> Review date: 2026-07-13
> Branch: `feature/wiki-v2-phase6-migration-feedback`
> Plan: `docs/superpowers/plans/2026-07-13-wiki-v2-phase6-migration-feedback-eval.md`
> Spec: `docs/superpowers/specs/2026-07-07-canonical-wiki-claim-provenance-design.md` §Phase 6
> Contract: `docs/architecture/wiki-v2-claim-merge-contract.md` (matcher 语义未改)

## Scope and Result

Phase 6 delivers dual-track → canonical migration, claim-layer user feedback,
provenance validation, and knowledge evolution evaluation. **Accepted.**

## Delivered

| Task | Deliverable |
|---|---|
| T6.1 | `WikiV2Migrator` dry-run / apply (lock+backup) / rollback; CLI `wiki migrate-v2` |
| T6.2 | `WikiValidator.validate_canonical_store` + CLI `wiki validate [--strict]` |
| T6.3 | `WikiFeedbackService` confirm/reject/correct/needs_review + CLI `wiki claims` |
| T6.4 | `evals/run_knowledge_evolution_eval.py` (8 gated metrics, Overall PASS) |
| Docs | `docs/migration/wiki-v2-migration.md`; version → 1.6.0 |

## Iron Rule Adherence

- Migration never auto-forces `canonical_v2.mode=primary` (suggestion only).
- Migrated claims are `draft` / `unsupported` only — never auto-active without review.
- Feedback writes only via `WikiRepository.transaction`; does not touch Raw Source.
- New services pure constructor DI; C6 guard covers migrator + feedback.
- C2 5 xfail preserved; claim merge contract unchanged.
- `ALLOWED_DIRECT_WRITES` remains empty.

## Verification

```text
pytest -q
1516 passed, 2 skipped, 5 xfailed
  (Phase 5 baseline 1497; Phase 6 +19 tests)

ruff check src tests evals tools scripts
All checks passed

mypy src
Success (source files)

python evals/run_knowledge_evolution_eval.py
Overall: PASS (8 gated metrics)

python evals/run_retrieval_eval.py --all
(recorded at gate time)

python evals/run_wiki_eval.py
(recorded at gate time)
```

## Residual / Follow-ups

- Projection parity metric is skip-pass when no projection injected in the
  standalone evolution fixture; production cutover should run with real projection.
- MCP tools `wiki_apply_feedback` / `wiki_validate` (admin profile) deferred —
  CLI path complete; can wire MCP without changing service contracts.
- C2 matcher xfail (unit/model/region/negation/intensity) still open.
