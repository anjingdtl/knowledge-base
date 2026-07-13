# Verified Hybrid Eval Report

- Total cases: **175**
- Telecom cases: **71**
- Elapsed: **5.13 ms**
- Overall: **PASS**

## Core metrics

| Metric | Value |
|---|---:|
| Raw correct | 1.0000 |
| Wiki correct | 1.0000 |
| Hybrid correct | 1.0000 |
| Hybrid ≥ Raw | True |
| Citation correctness | 1.0000 |
| Stale serving rate | 0.0000 |
| Unsupported serving rate | 0.0000 |
| Conflict detection recall | 1.0000 |
| Raw fallback success | 1.0000 |
| Evidence resolvability | 1.0000 |

## Gates

- `PASS` case_count_ge_150
- `PASS` hybrid_ge_raw_correctness
- `PASS` stale_serving_rate_zero
- `PASS` unsupported_serving_rate_zero
- `PASS` citation_correctness_ge_0_95
- `PASS` conflict_detection_recall_ge_0_90
- `PASS` raw_fallback_success_one
- `PASS` evidence_resolvability_ge_0_99
- `PASS` all_cases_hybrid_ge_raw

## By category

| Category | N | Raw | Wiki | Hybrid |
|---|---:|---:|---:|---:|
| concept_summary | 15 | 1.000 | 1.000 | 1.000 |
| conflict | 15 | 1.000 | 1.000 | 1.000 |
| cross_document | 25 | 1.000 | 1.000 | 1.000 |
| freshness_stale | 10 | 1.000 | 1.000 | 1.000 |
| location_media | 20 | 1.000 | 1.000 | 1.000 |
| no_answer | 15 | 1.000 | 1.000 | 1.000 |
| numeric_unit | 15 | 1.000 | 1.000 | 1.000 |
| scope_condition | 10 | 1.000 | 1.000 | 1.000 |
| single_fact | 25 | 1.000 | 1.000 | 1.000 |
| unsupported_guard | 5 | 1.000 | 1.000 | 1.000 |
| wiki_fallback | 5 | 1.000 | 1.000 | 1.000 |
| zh_abbreviation | 15 | 1.000 | 1.000 | 1.000 |

## Failures (showing 0)

_None_

## Notes

- Offline deterministic eval (no embedding / LLM).
- Real-model optional eval can wrap the same cases later.
