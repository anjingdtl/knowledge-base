# Production Pilot Gate Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four production-pilot hard-gate gaps — Precision@5, Numeric units, Routing re-validation, and real ask/Citation false refusals — without relaxing Spec safety rules or mutating formal `data/kb.db` during evaluation.

**Architecture:** Keep retrieval/MCP tool surfaces stable. Add a small pure post-processing layer (`dedupe_by_knowledge_id`) shared by MCP `search` / FTS paths. Harden numeric unit extraction for compound units (`珠/米`). Fix ask over-refusal by distinguishing **in-corpus institutional evidence** from **current-info / out-of-corpus** gates. Re-run formal read-only MCP suites and update the pilot report with honest numerators/denominators.

**Tech Stack:** Python 3.10+, pytest, existing `SearchService` / MCP tools / `evals/production_pilot_metrics.py` / `scripts/production_pilot_mcp_harness.py`

**Branch:** `fix/mcp-production-pilot-final-validation` (continue)

**Success criteria (must all hold or report remains FAIL):**

| Gate | Target |
|------|--------|
| Precision@5 | ≥ 0.70 on formal hybrid sample n≥40 |
| Recall@5 | still ≥ 0.90 |
| Numeric Top1 unit | ≥ 0.95 (applicable denom) |
| Numeric Top3 expected doc | ≥ 0.95 |
| Routing Mode / Tool | ≥ 0.95 after re-run |
| Answer completion (real provider sample ≥10) | ≥ 0.90 |
| Citation completeness / correctness | ≥ 0.95 when applicable |
| Formal `kb.db` sha | unchanged during eval |

**Non-goals:** Threshold thrashing to greenwash; deleting failing GT rows; claiming Hybrid+Reranker PASS without evidence; writing to formal DB in eval.

---

## File map

| File | Responsibility |
|------|----------------|
| `src/services/result_dedupe.py` | **Create** — document-level hit dedupe |
| `src/mcp/tools/retrieval.py` | Apply dedupe in `search` / `search_fulltext` result paths |
| `src/services/numeric_unit_match.py` | Compound unit `珠/米` extraction + stronger demote |
| `src/services/relevance_gate.py` | Reduce false no-answer on strong in-corpus title/term hits |
| `src/services/route_engine.py` | Already partially fixed; add file_type structured rules if needed |
| `tests/services/test_result_dedupe.py` | **Create** |
| `tests/services/test_numeric_unit_match.py` | Extend / create |
| `tests/services/test_relevance_gate_in_corpus.py` | **Create** |
| `scripts/production_pilot_mcp_harness.py` | Capture precision failure rows; optional rerank flag |
| `artifacts/production-pilot-final-validation/*` | Re-run evidence |
| `docs/reports/mcp-production-pilot-gate-remediation-2026-07-16.md` | **Create** delta report |

---

### Task 1: Document-level dedupe (Precision foundation)

**Files:**
- Create: `src/services/result_dedupe.py`
- Create: `tests/services/test_result_dedupe.py`
- Modify: `src/mcp/tools/retrieval.py` (`search` `_normalize_hits` / return paths; `search_fulltext` list)

- [ ] **Step 1: Write failing tests**

```python
# tests/services/test_result_dedupe.py
from src.services.result_dedupe import dedupe_by_knowledge_id

def test_keeps_highest_score_per_knowledge_id():
    items = [
        {"knowledge_id": "a", "score": 0.2, "text": "low"},
        {"knowledge_id": "a", "score": 0.9, "text": "high"},
        {"knowledge_id": "b", "score": 0.5, "text": "b"},
    ]
    out = dedupe_by_knowledge_id(items)
    assert [x["knowledge_id"] for x in out] == ["a", "b"]
    assert out[0]["text"] == "high"

def test_empty_knowledge_id_kept_separately():
    items = [
        {"knowledge_id": "", "score": 0.1, "block_id": "b1"},
        {"knowledge_id": "", "score": 0.2, "block_id": "b2"},
    ]
    out = dedupe_by_knowledge_id(items)
    assert len(out) == 2
```

- [ ] **Step 2: Run tests — expect FAIL (module missing)**

```powershell
pytest tests/services/test_result_dedupe.py -q
```

- [ ] **Step 3: Implement**

```python
# src/services/result_dedupe.py
def dedupe_by_knowledge_id(items: list[dict], *, score_keys=("score", "fts_score", "similarity")) -> list[dict]:
    """Keep one hit per knowledge_id (highest score); preserve relative order of winners."""
    ...
```

- [ ] **Step 4: Wire into `search` after normalize, before ranking/gate**

In `src/mcp/tools/retrieval.py` `_normalize_hits` or immediately after building hit lists, call `dedupe_by_knowledge_id`.

- [ ] **Step 5: Tests pass + commit**

```powershell
pytest tests/services/test_result_dedupe.py -q
git commit -m "fix(retrieval): dedupe search hits by knowledge_id for precision"
```

---

### Task 2: Numeric compound unit extraction + ranking

**Files:**
- Modify: `src/services/numeric_unit_match.py`
- Create/extend: `tests/services/test_numeric_unit_match_compound.py`

- [ ] **Step 1: Failing tests**

```python
def test_extract_beads_per_meter_as_compound_unit():
    hits = extract_number_units("规格 60珠/米 灯带")
    assert any(h.unit in ("珠/米", "珠") for h in hits)

def test_query_meters_demotes_beads_per_meter():
    r = score_numeric_unit_match("60 米", "规格 60珠/米 灯带")
    assert r["features"]["number_match_unit_mismatch"] is True
    assert r["score_delta"] < 0

def test_query_meters_boosts_plain_meters():
    r = score_numeric_unit_match("60 米", "长度 60米 光纤")
    assert r["features"]["exact_number_unit_match"] is True
    assert r["score_delta"] > 0
```

- [ ] **Step 2: Implement compound unit regex before simple unit**

Match `珠/米` (and similar `X/米`) as atomic unit so plain `米` does not false-positive match.

- [ ] **Step 3: pytest green + commit**

```powershell
pytest tests/services/test_numeric_unit_match_compound.py -q
git commit -m "fix(search): compound numeric units for 珠/米 vs 米 ranking"
```

---

### Task 3: Reduce false no-answer on in-corpus institutional queries

**Files:**
- Modify: `src/services/relevance_gate.py` (`evaluate_evidence`)
- Create: `tests/services/test_relevance_gate_in_corpus.py`
- Optionally touch: `src/mcp/tools/retrieval.py` ask path only if gate API changes

**Problem:** ANS samples with supporting docs in GT still return `answer_mode=no_answer`.

- [ ] **Step 1: Write failing test for strong title+term evidence**

```python
def test_accepts_strong_title_match_even_if_semantic_score_low():
    items = [{
        "knowledge_id": "d1",
        "title": "中国电信广西公司企业微信运营管理办法",
        "text": "本办法规范企业微信运营管理……",
        "score": 0.1,
    }]
    decision = evaluate_evidence("企业微信运营管理办法的主题是什么", items, threshold=0.35)
    assert decision["accept"] is True
    assert decision["items"]
```

- [ ] **Step 2: Implement accept rule**

If top candidate has:
- title contains ≥2 query terms OR title substring match of core noun phrase, **and**
- not `is_current_information_query`,
then accept even when raw semantic score &lt; threshold (still reject unit-mismatch-only hits).

- [ ] **Step 3: Regression — current-info still rejected**

```python
def test_current_info_still_rejected_by_caller():
    assert is_current_information_query("今天公司营收是多少") is True
```

- [ ] **Step 4: Commit**

```powershell
pytest tests/services/test_relevance_gate_in_corpus.py -q
git commit -m "fix(rag): accept strong in-corpus title evidence to cut false no-answer"
```

---

### Task 4: Structured file_type routing rules (Routing precision)

**Files:**
- Modify: `src/services/route_engine.py` `RuleRouter._try_rule_based`
- Create: `tests/services/test_structured_file_type_routing.py`

- [ ] **Step 1: Failing tests**

```python
def test_list_md_documents_routes_structured_file_type():
    r = RuleRouter(db=None).route("列出所有 md 文档")
    assert r is not None
    assert r["mode"] == "structured"
    # query_spec filter contains file_type md
```

- [ ] **Step 2: Add regex for `file_type` / `md|pdf|docx|xlsx` listing patterns before returning None**

- [ ] **Step 3: Commit**

```powershell
pytest tests/services/test_structured_file_type_routing.py tests/stability/test_graph_routing_priority.py -q
git commit -m "fix(router): structured file_type rules for list-all md/pdf queries"
```

---

### Task 5: Harness — precision failure dump + rerun suite

**Files:**
- Modify: `scripts/production_pilot_mcp_harness.py`
- Run artifacts under `artifacts/production-pilot-final-validation/`

- [ ] **Step 1: After scoring retrieval, write `precision-failures.jsonl`** for rows where top5 ∩ rel is empty or precision contribution &lt; 0.4

- [ ] **Step 2: Re-run formal stdio suite (read-only)**

```powershell
python scripts/production_pilot_mcp_harness.py --transport both --answer-limit 10 --retrieval-limit 40 --skip-concurrency
```

- [ ] **Step 3: Recompute metrics; assert formal DB sha unchanged**

- [ ] **Step 4: Commit artifacts + harness**

```powershell
git commit -m "test(mcp): re-run pilot suite after precision/numeric/routing fixes"
```

---

### Task 6: Routing re-validation only (fast path)

- [ ] **Step 1: Run routing-only subset** (or full suite if already in Task 5)

- [ ] **Step 2: Record Mode/Tool/Arg/Task metrics in `routing-rerun.json`**

- [ ] **Step 3: If Mode &lt; 0.95, inspect failures — fix only if clear product bug, else document residual FAIL**

---

### Task 7: Engineering gates + delta report

**Files:**
- Create: `docs/reports/mcp-production-pilot-gate-remediation-2026-07-16.md`
- Update: `PROGRESS.md` status line

- [ ] **Step 1:**

```powershell
ruff check src/services/result_dedupe.py src/services/numeric_unit_match.py src/services/relevance_gate.py src/services/route_engine.py src/mcp/tools/retrieval.py
pytest tests/services/test_result_dedupe.py tests/services/test_numeric_unit_match_compound.py tests/services/test_relevance_gate_in_corpus.py tests/services/test_structured_file_type_routing.py tests/eval -q
```

- [ ] **Step 2: Write delta report** comparing before/after gates; final line only:

```text
达到生产试点门槛
```
or
```text
未达到生产试点门槛
```

- [ ] **Step 3: Final commit**

```powershell
git commit -m "docs(validation): gate remediation delta report"
```

---

## Execution notes

1. **TDD always** for Tasks 1–4.  
2. **No formal DB writes** in harness.  
3. Do not lower `no_match_threshold` globally to force Citation green.  
4. Prefer surgical ranking/dedupe/gate fixes over metric-script changes.  
5. Commit after each task.

## Self-review

| Requirement | Task |
|-------------|------|
| Precision@5 | T1 + T5 |
| Numeric units | T2 + T5 |
| Routing re-run | T4 + T6 |
| Citation / false no-answer | T3 + T5 |
| Formal DB safe | T5 sha check |
| Honest report | T7 |
