# Retrieval Quality Evaluation

This document describes the retrieval quality evaluation system for ShineHeKnowledge.

## Overview

The retrieval eval measures how well the search pipeline finds the right source documents
for a given query. It runs offline against a fixed set of fixture documents, requiring
no LLM or external API keys.

## Metrics

| Metric | Description |
|--------|-------------|
| **Recall@5** | Fraction of expected source documents found in the top-5 results |
| **MRR** | Mean Reciprocal Rank — 1/(rank of first correct result) |
| **nDCG@10** | Normalized Discounted Cumulative Gain at rank 10 |
| **No-Answer Accuracy** | Fraction of out-of-scope queries that correctly return no relevant results |
| **Citation Location Completeness** | Share of returned citations with a valid path, block ID, and non-empty source location |

## Current Baseline

The checked-in `evals/baselines/local.json` baseline was refreshed on June 13, 2026:

| Metric | Baseline |
|--------|----------|
| Recall@5 | 0.8667 |
| MRR | 0.7800 |
| nDCG@10 | 0.7938 |
| No-Answer Accuracy | 0.6667 |
| Citation Location Completeness | 1.0000 |

## Running Locally

### Full eval (all datasets)

```bash
python evals/run_retrieval_eval.py --all --fake-embedding
```

### Specific dataset

```bash
python evals/run_retrieval_eval.py --dataset retrieval_zh --fake-embedding
```

### With baseline comparison

```bash
python evals/run_retrieval_eval.py \
  --all \
  --fake-embedding \
  --baseline evals/baselines/local.json \
  --max-regression 0.02
```

### Save report

```bash
python evals/run_retrieval_eval.py \
  --all \
  --fake-embedding \
  --report json \
  --output report.json
```

## Datasets

| File | Description | Queries |
|------|-------------|---------|
| `evals/datasets/retrieval_zh.yaml` | Chinese keyword retrieval | 5 |
| `evals/datasets/retrieval_code.yaml` | Code retrieval | 3 |
| `evals/datasets/retrieval_table.yaml` | API/structured retrieval | 2 |
| `evals/datasets/retrieval_no_answer.yaml` | Out-of-scope queries | 3 |

## Fixtures

Fixture documents live in `evals/fixtures/` and cover different content types:

- `architecture.md` — system architecture
- `api_guide.md` — API reference
- `troubleshooting.md` — FAQ / troubleshooting
- `code_example.py` — Python source code
- `config_reference.md` — configuration reference
- `distractor.md` — topically similar but irrelevant content

## Updating Baselines

When you change the search pipeline and expect metric changes:

1. Run the eval locally to get new metrics.
2. Update `evals/baselines/local.json` with the new values.
3. In your PR, include a table showing old vs. new baseline values and explain why.

```bash
python evals/run_retrieval_eval.py \
  --all \
  --fake-embedding \
  --baseline evals/baselines/local.json \
  --update-baseline
```

## CI Integration

The `retrieval-eval` job in `.github/workflows/ci.yml` runs on every push and PR:

1. Installs core + parsers + dev dependencies (no API keys needed).
2. Validates eval datasets (`tests/test_eval_datasets.py`).
3. Runs retrieval eval with `--fake-embedding` for deterministic results.
4. Compares against `evals/baselines/local.json` (max 5% regression allowed).
5. Uploads the JSON report as a CI artifact.

The CI job does **not** download large models or call external APIs.

## Adding New Queries

To add a new retrieval query:

1. Add the query to the appropriate `evals/datasets/retrieval_*.yaml` file.
2. Ensure `expected_sources` points to an existing fixture in `evals/fixtures/`.
3. Verify `block_contains` text actually appears in the fixture.
4. Run `pytest tests/test_eval_datasets.py` to validate.
5. Run the eval to verify the query behaves as expected.
