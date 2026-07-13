# Canonical Wiki V2 Phase 4A Shadow Real Data Review

> Review date: 2026-07-09
> Branch: `feature/wiki-v2-phase4a-shadow`
> Commit under review: `462f9a9 feat(wiki-v2): integrate shadow canonical workflow`
> Plan gate: `docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md` §6 Phase 4A

## Scope

Phase 4A requires the claim flow to run after real ingest in `shadow` mode, write only to an isolated shadow area, and produce a legacy-vs-V2 difference report. Exit requires at least one real personal knowledge-base dataset run plus sampled manual review.

This review uses an existing indexed local knowledge item from `data/kb.db`:

- `knowledge_id`: `e3eb0f42-935e-4291-8889-06510a100a0a`
- `content_hash`: `010cc2f3edbab5ef356704a31cf328654e5e3f548cbb68d3fdc9027190c9a9c7`
- `updated_at`: `2026-06-24T16:06:45.352138`
- indexed blocks: 1
- source block used by all sampled claims: `c9cccdb4-1261-43a8-8beb-2e5c358e597f`

Runtime settings were set in-process only:

- `knowledge_workflow.mode=wiki_first`
- `wiki.canonical_v2.mode=shadow`
- `wiki.claims.max_llm_calls_per_ingest=1`
- `wiki.max_llm_calls_per_ingest=0` to avoid legacy entity-update LLM calls during the shadow gate run

## Real Run Result

Archived report: `artifacts/eval/wiki-v2-phase4a-shadow-e3eb0f42.json`

| Metric | Result |
|---|---:|
| shadow status | completed |
| claims extracted | 8 |
| new claims | 8 |
| auto merged | 0 |
| unresolved | 0 |
| conflicts | 0 |
| evidence missing | 0 |
| LLM calls | 1 |
| latency | 13,527 ms |
| committed to shadow repo | true |
| formal `wiki/claims` changed | no |
| formal `data/wiki_projection_outbox.jsonl` changed | no |

Shadow outputs were written under `wiki/_shadow/`:

- `wiki/_shadow/claims/*.yaml`: 8 draft claims
- `wiki/_shadow/_meta/projection_outbox.jsonl`: 8 `claim.created` events with shadow tx id
- `wiki/_shadow/reports/e3eb0f42-935e-4291-8889-06510a100a0a.json`: per-ingest summary report

## Sampled Claim Review

Three sampled claims were checked against the source block and canonical evidence requirements:

| Claim | Statement summary | Status | Evidence check | Review |
|---|---|---|---|---|
| `claim_1d0aba0d-68c7-4ae0-ba5d-68f2066b6206` | The competition cycle is 2026-05 to 2026-10. | draft | `knowledge_id`, `block_id`, `source_revision`, `excerpt_hash`, and `location` present | Supported by source block metadata and text. |
| `claim_216a1b0f-f2a8-4a3b-814d-601c527ebd4d` | Participants are channel managers and frontline all-service sales operators. | draft | Complete evidence chain to block `c9cccdb4...` | Supported by source block text. |
| `claim_14d13659-783d-43a7-b09b-0565d96ffedf` | A core tactic is promoting integrated existing-and-new customer scale growth. | draft | Complete evidence chain to block `c9cccdb4...` | Supported by source block text. |

All 8 generated claims were also checked structurally:

- status is `draft`, not `active`
- each claim has at least one `supports` Evidence
- each Evidence includes `knowledge_id`
- each Evidence includes `block_id`
- each Evidence includes `source_revision`
- each Evidence includes `excerpt_hash`
- each Evidence includes `location`

## Issue Found And Fixed

The first real shadow run exposed a true model-output compatibility issue: the live LLM returned a `<think>...</think>` prefix before JSON, and `ClaimExtractor._parse_llm_json` only accepted pure JSON or fenced JSON. This caused 0 extracted claims despite a successful LLM call.

Fix:

- Added regression test `test_llm_json_after_think_prefix_is_parsed`
- Extended `_parse_llm_json` to fall back to parsing the first `{` through the last `}` when direct parsing fails

After the fix, the same real data run extracted 8 claims with 0 errors and 0 warnings.

## Phase 4A Exit Assessment

Phase 4A exit conditions are satisfied for one real local dataset sample:

- raw/legacy workflow was not blocked
- legacy source/index/log outputs remained functional
- V2 output stayed isolated under `wiki/_shadow/`
- formal canonical claims remained untouched
- formal projection outbox remained untouched
- report contains required stats: new claims, auto merges, unresolved, conflicts, evidence missing, diff, LLM calls, latency
- sampled claims have complete Evidence and are traceable back to the raw source block

Residual risks before Phase 4B:

- This is a one-item sample; canary selection should remain narrow.
- The C2 golden xfailed cases remain unresolved and should continue to gate matcher conservatism.
- Console display of some local titles is encoding-sensitive, but YAML/report files preserve UTF-8 content.

