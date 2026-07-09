# Canonical Wiki V2 Phase 4B Canary Review

> Review date: 2026-07-09
> Branch: `feature/wiki-v2-phase4a-shadow`
> Plan gate: `docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md` §6 Phase 4B

## Scope

Phase 4B requires explicitly allowlisted knowledge objects to use Canonical V2 as the formal write path while keeping legacy fallback available. High-risk merge decisions must be forced to review, automatic publish must stay disabled, each operation must expose a transaction id, and canary data must pass projection parity with rollback evidence.

Implemented in this phase:

- `WikiCanaryWorkflow` formal write path with `knowledge_ids/source_paths` allowlist.
- `KnowledgeWorkflowService` canary-mode integration with failure isolation.
- Container injection for the canary workflow.
- `MergeResult.tx_id` propagation from `WikiRepository.transaction()`.
- Canary review gate for `contradicts`, `supersedes`, and low-confidence `refines`.
- Projection processing + parity verification, with rebuild on detected drift.
- Merge evidence dedupe fix so repeated `supports` evidence does not bump revision or break projection uniqueness.

## Findings

No blocking findings remain for Phase 4B.

The real-data canary probe initially exposed a projection uniqueness failure: repeated ingest could add support evidence with the same source/block key, then projection reported a unique constraint error. Root cause was in `WikiMergeEngine._do_supports_or_duplicate`: `supports` only deduped when action was `duplicate`. The fix now dedupes existing evidence keys for both actions and skips staging when no actual evidence is added.

## Real Data Probe

Archived report: `artifacts/eval/wiki-v2-phase4b-canary-2abec2ec.json`

Sample:

- knowledge_id: `2abec2ec-fe20-4fc9-834b-743a52764cdb`
- mode: deterministic canary probe against a temporary copy of local `kb.db`
- repository: temporary formal V2 wiki under `.temp/wiki-v2-phase4b-canary/wiki`

Result:

- status: `passed`
- run 1: created one draft claim, tx_id `tx_b93538aeb2cc`, projection parity findings `0`
- run 2: duplicate evidence skipped, tx_id `tx_694c3b1d491e`, projection parity findings `0`
- final claim revision stayed `1`, evidence_count stayed `1`
- rollback probe raised inside a transaction and left no visible staged claim

## Acceptance Mapping

| Requirement | Evidence |
|---|---|
| Explicit allowlist only | `test_canary_skips_objects_outside_allowlist_without_extracting` |
| Formal V2 write for canary | `test_canary_writes_to_formal_repository_and_reports_tx_id_and_parity` |
| High-risk actions forced review | `test_canary_forces_high_risk_and_low_confidence_refines_to_review` |
| Transaction id exposed | `test_merge_result_exposes_committed_transaction_id` + real probe tx ids |
| Projection drift repair | `test_canary_rebuilds_projection_when_parity_drift_is_detected` |
| Rollback/no half-write | `test_transaction_rollback_on_failure`, transaction recovery tests, real rollback probe |
| Core retrieval no regression | `python evals/run_retrieval_eval.py --all` Overall PASS |

## Verification

Fresh commands run before commit:

```text
pytest tests/test_wiki_merge_engine.py::TestSupports::test_supports_dedupes_existing_evidence_key tests/test_wiki_merge_engine.py tests/test_wiki_canary_workflow.py tests/test_wiki_projection.py -q
41 passed

pytest tests/test_wiki_canary_workflow.py tests/test_wiki_shadow_workflow.py tests/test_knowledge_workflow.py tests/test_wiki_merge_engine.py tests/test_wiki_projection.py tests/test_wiki_canonical_mode.py tests/test_wiki_repository.py tests/test_wiki_v2_transaction_recovery.py -q
87 passed

pytest -q
1425 passed, 2 skipped, 5 xfailed, 8 warnings

ruff check src tests evals tools scripts
All checks passed

mypy src tools
Success: no issues found in 188 source files

python evals/run_retrieval_eval.py --all
Overall: PASS

python evals/run_wiki_eval.py
source_coverage: 0.0
cross_page_update_rate: 0.9545
orphan_page_rate: 0.0
query_save_rate: 0.0
stale_claim_ratio: 0.0
```

## Exit Assessment

Phase 4B exit conditions are satisfied for the current staged rollout:

- Canary objects are explicitly listable by config.
- Formal V2 writes occur only for allowlisted objects.
- High-risk semantic updates are forced to review.
- Automatic publish remains disabled.
- Canary reports expose transaction ids.
- Projection parity is checked and drift repair is covered.
- Rollback/no-half-write behavior is tested with unit and real probe evidence.
- Core retrieval and wiki evals show no regression.

Residual risks before Phase 4C:

- Review queue volume is unknown until broader canary rollout.
- `primary` still requires replacing the remaining legacy direct write paths and shrinking the canonical write guard allowlist.
- The five conservative matcher xfails remain a semantic precision gap to address through later matcher/eval work.
