"""Offline retrieval evaluation — index fixtures and measure search quality.

This script creates a temporary environment, indexes fixture documents,
runs search queries, and compares results against golden sources.

Usage:
    python evals/run_retrieval_eval.py --all
    python evals/run_retrieval_eval.py --dataset retrieval_zh
    python evals/run_retrieval_eval.py --all --baseline evals/baselines/local.json
    python evals/run_retrieval_eval.py --all --fake-embedding --output report.json
    python evals/run_retrieval_eval.py --all --max-regression 0.02
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
import tempfile
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from collections import Counter

import yaml

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

EVALS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = EVALS_DIR / "fixtures"
DATASETS_DIR = EVALS_DIR / "datasets"
BASELINES_DIR = EVALS_DIR / "baselines"


# ---------------------------------------------------------------------------
# Metric dataclass (mirrors evals/metrics.py EvalMetrics for offline use)
# ---------------------------------------------------------------------------

@dataclass
class RetrievalMetrics:
    recall_at_5: float = 0.0
    mrr: float = 0.0
    ndcg_at_10: float = 0.0
    no_answer_accuracy: float = 0.0
    citation_location_completeness: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    total_queries: int = 0


# ---------------------------------------------------------------------------
# Metric computation functions (work on raw result dicts)
# ---------------------------------------------------------------------------

def _result_paths(results: list[dict]) -> list[str]:
    """Extract identifiable paths/titles from a result list."""
    paths = []
    for r in results:
        # Try citation path
        path = r.get("citation", {}).get("path", "")
        if not path:
            path = r.get("metadata", {}).get("source_path", "")
        if not path:
            path = r.get("source_path", "")
        if path:
            paths.append(path)
        # Also include title as fallback identifier
        title = r.get("title", "")
        if title:
            paths.append(title)
    return paths


def compute_recall(results: list[dict], expected_paths: list[str]) -> float:
    """Recall@5: fraction of expected source paths found in top-5 results."""
    if not expected_paths:
        return 1.0  # no_answer case — nothing to find
    result_paths = _result_paths(results[:5])
    found = sum(
        1 for ep in expected_paths
        if any(ep in rp or Path(rp).name == Path(ep).name for rp in result_paths)
    )
    return found / len(expected_paths) if expected_paths else 1.0


def compute_mrr(results: list[dict], expected_paths: list[str]) -> float:
    """MRR: reciprocal rank of first correct result."""
    if not expected_paths:
        return 1.0
    for i, r in enumerate(results[:10]):
        path = (
            r.get("citation", {}).get("path", "")
            or r.get("metadata", {}).get("source_path", "")
            or r.get("source_path", "")
        )
        title = r.get("title", "")
        for ep in expected_paths:
            if ep in path or ep in title or Path(ep).name == Path(title).name:
                return 1.0 / (i + 1)
    return 0.0


def compute_ndcg(results: list[dict], expected_paths: list[str], k: int = 10) -> float:
    """nDCG@10: normalized discounted cumulative gain."""
    if not expected_paths:
        return 1.0
    relevances = []
    for r in results[:k]:
        path = (
            r.get("citation", {}).get("path", "")
            or r.get("metadata", {}).get("source_path", "")
            or r.get("source_path", "")
        )
        title = r.get("title", "")
        relevant = any(
            ep in path or ep in title or Path(ep).name == Path(title).name
            for ep in expected_paths
        )
        relevances.append(1.0 if relevant else 0.0)

    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances))
    n_rel = min(int(sum(relevances)), k)
    ideal_rels = [1.0] * n_rel + [0.0] * (k - n_rel)
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal_rels[:k]))
    return dcg / idcg if idcg > 0 else 0.0


# ---------------------------------------------------------------------------
# Lightweight offline indexer (fake-embedding / BM25 keyword mode)
# ---------------------------------------------------------------------------

class OfflineIndex:
    """In-memory index for fixture documents — supports keyword search."""

    def __init__(self):
        self.documents: list[dict] = []
        self._doc_freq: Counter = Counter()
        self._n_docs: int = 0

    def _tokenize(self, text: str) -> list[str]:
        """Simple whitespace + lowercase tokenizer."""
        text = text.lower()
        return re.findall(r'[a-zA-Z0-9_\u4e00-\u9fff]+', text)

    def index_fixture(self, path: Path, content: str):
        """Index a single fixture file as one or more chunks."""
        # Split by headings (## or ###) for markdown, or by blank lines
        if path.suffix in ('.md', '.markdown'):
            chunks = self._split_markdown(content)
        elif path.suffix == '.py':
            chunks = self._split_python(content)
        else:
            chunks = [{"heading": path.stem, "text": content}]

        for i, chunk in enumerate(chunks):
            doc = {
                "source_path": str(path.name),
                "source_full_path": str(path),
                "title": path.name,
                "heading": chunk.get("heading", ""),
                "text": chunk["text"],
                "chunk_index": i,
                "tokens": self._tokenize(chunk["text"]),
            }
            self.documents.append(doc)

        self._n_docs += 1
        # Update document frequency for IDF
        all_tokens = set()
        for chunk in chunks:
            all_tokens.update(self._tokenize(chunk["text"]))
        for token in all_tokens:
            self._doc_freq[token] += 1

    def _split_markdown(self, content: str) -> list[dict]:
        """Split markdown by headings."""
        chunks = []
        current_heading = ""
        current_lines = []

        for line in content.split('\n'):
            if line.startswith('#'):
                if current_lines:
                    chunks.append({
                        "heading": current_heading,
                        "text": '\n'.join(current_lines).strip(),
                    })
                current_heading = line.lstrip('#').strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            chunks.append({
                "heading": current_heading,
                "text": '\n'.join(current_lines).strip(),
            })

        return [c for c in chunks if c["text"].strip()]

    def _split_python(self, content: str) -> list[dict]:
        """Split Python by top-level definitions."""
        chunks = []
        current_lines = []
        current_name = ""

        for line in content.split('\n'):
            if line.startswith('def ') or line.startswith('class '):
                if current_lines:
                    chunks.append({
                        "heading": current_name,
                        "text": '\n'.join(current_lines).strip(),
                    })
                match = re.match(r'(def|class)\s+(\w+)', line)
                current_name = match.group(2) if match else line.strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            chunks.append({
                "heading": current_name,
                "text": '\n'.join(current_lines).strip(),
            })

        return [c for c in chunks if c["text"].strip()]

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """BM25 keyword search over indexed documents."""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        # Also handle Chinese queries — add character bigrams
        if any('\u4e00' <= c <= '\u9fff' for c in query):
            for i in range(len(query) - 1):
                bigram = query[i:i+2].lower()
                if '\u4e00' <= bigram[0] <= '\u9fff':
                    query_tokens.append(bigram)

        scored = []
        avg_dl = sum(len(d["tokens"]) for d in self.documents) / max(len(self.documents), 1)
        k1 = 1.5
        b = 0.75

        for doc in self.documents:
            score = 0.0
            doc_tokens = doc["tokens"]
            dl = len(doc_tokens)
            tf_map = Counter(doc_tokens)

            # Also build a bigram index for Chinese matching
            doc_text = doc["text"].lower()
            for i in range(len(doc_text) - 1):
                bigram = doc_text[i:i+2]
                if '\u4e00' <= bigram[0] <= '\u9fff':
                    tf_map[bigram] = tf_map.get(bigram, 0) + 1

            for token in query_tokens:
                tf = tf_map.get(token, 0)
                if tf == 0:
                    continue
                df = self._doc_freq.get(token, 0)
                idf = math.log((self._n_docs - df + 0.5) / (df + 0.5) + 1)
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
                score += idf * tf_norm

            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, doc in scored[:top_k]:
            results.append({
                "source_path": doc["source_path"],
                "title": doc["title"],
                "text": doc["text"],
                "heading": doc["heading"],
                "score": score,
                "metadata": {
                    "source_path": doc["source_path"],
                    "chunk_index": doc["chunk_index"],
                },
                "citation": {
                    "path": doc["source_path"],
                },
            })
        return results


# ---------------------------------------------------------------------------
# Fake embedding generator (deterministic, for CI)
# ---------------------------------------------------------------------------

def fake_embed(text: str, dim: int = 1024) -> list[float]:
    """Generate a deterministic pseudo-embedding from text hash.

    Not semantically meaningful — used only for structural testing
    of the vector pipeline without real API calls.
    """
    h = hashlib.sha512(text.encode("utf-8")).digest()
    # Expand hash to fill dim floats
    raw = (h * (dim * 4 // len(h) + 1))[:dim * 4]
    import struct
    floats = list(struct.unpack(f'{dim}f', raw[:dim * 4]))
    # Normalize
    norm = math.sqrt(sum(f * f for f in floats)) or 1.0
    return [f / norm for f in floats]


# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------

def load_dataset(path: Path) -> list[dict]:
    """Load a YAML retrieval dataset."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Dataset must be a YAML list, got {type(data)}")
    return data


def build_index(use_fake_embedding: bool = False) -> OfflineIndex:
    """Build an offline index from all fixture documents."""
    index = OfflineIndex()
    for fixture_path in sorted(FIXTURES_DIR.glob("*")):
        if fixture_path.is_file():
            content = fixture_path.read_text(encoding="utf-8")
            index.index_fixture(fixture_path, content)
    return index


def run_single_query(index: OfflineIndex, item: dict) -> dict:
    """Run a single retrieval query and evaluate against expected sources."""
    query = item["query"]
    expected_sources = item.get("expected_sources", [])
    must_not_match = item.get("must_not_match", [])
    category = item.get("category", "keyword")

    t0 = time.monotonic()
    results = index.search(query, top_k=10)
    latency_ms = (time.monotonic() - t0) * 1000

    expected_paths = [src["path"].replace("fixtures/", "") for src in expected_sources]
    must_not_paths = [src["path"].replace("fixtures/", "") for src in must_not_match]

    recall = compute_recall(results, expected_paths)
    mrr = compute_mrr(results, expected_paths)
    ndcg = compute_ndcg(results, expected_paths)

    # Check must_not_match
    result_filenames = set()
    for r in results:
        sp = r.get("source_path", "")
        if sp:
            result_filenames.add(Path(sp).name)
    must_not_violated = any(
        Path(mnp).name in result_filenames for mnp in must_not_paths
    )

    # For no_answer queries, check that no expected sources were returned
    no_answer_correct = True
    if category == "no_answer":
        no_answer_correct = len(results) == 0 or all(
            r.get("score", 0) < 0.1 for r in results
        )

    return {
        "query": query,
        "category": category,
        "recall": recall,
        "mrr": mrr,
        "ndcg": ndcg,
        "latency_ms": latency_ms,
        "must_not_violated": must_not_violated,
        "no_answer_correct": no_answer_correct,
        "results_count": len(results),
        "top_result": results[0] if results else None,
    }


def aggregate_retrieval_metrics(query_results: list[dict]) -> RetrievalMetrics:
    """Aggregate per-query results into summary metrics."""
    m = RetrievalMetrics()
    m.total_queries = len(query_results)

    if not query_results:
        return m

    # Standard retrieval metrics (exclude no_answer from recall/mrr/ndcg)
    retrieval_items = [q for q in query_results if q["category"] != "no_answer"]
    no_answer_items = [q for q in query_results if q["category"] == "no_answer"]

    if retrieval_items:
        m.recall_at_5 = sum(q["recall"] for q in retrieval_items) / len(retrieval_items)
        m.mrr = sum(q["mrr"] for q in retrieval_items) / len(retrieval_items)
        m.ndcg_at_10 = sum(q["ndcg"] for q in retrieval_items) / len(retrieval_items)

    if no_answer_items:
        m.no_answer_accuracy = (
            sum(1 for q in no_answer_items if q["no_answer_correct"])
            / len(no_answer_items)
        )

    # Latency stats
    latencies = sorted(q["latency_ms"] for q in query_results if q["latency_ms"] > 0)
    if latencies:
        n = len(latencies)
        p50_idx = max(0, min(n - 1, int(math.ceil(n * 0.5)) - 1))
        p95_idx = max(0, min(n - 1, int(math.ceil(n * 0.95)) - 1))
        m.latency_p50_ms = latencies[p50_idx]
        m.latency_p95_ms = latencies[p95_idx]

    return m


def compare_with_baseline(
    metrics: RetrievalMetrics,
    baseline_path: str,
    max_regression: float = 0.02,
) -> tuple[bool, list[str]]:
    """Compare current metrics against a baseline file.

    Returns (passed, list_of_warnings).
    """
    bp = Path(baseline_path)
    if not bp.exists():
        return True, [f"Baseline file not found: {baseline_path} — skipping comparison"]

    baseline = json.loads(bp.read_text(encoding="utf-8"))
    baseline_metrics = baseline.get("metrics", {})

    warnings = []
    passed = True

    comparisons = [
        ("recall_at_5", metrics.recall_at_5),
        ("mrr", metrics.mrr),
        ("ndcg_at_10", metrics.ndcg_at_10),
        ("no_answer_accuracy", metrics.no_answer_accuracy),
    ]

    for key, current_value in comparisons:
        baseline_value = baseline_metrics.get(key, 0.0)
        if baseline_value == 0.0 and current_value == 0.0:
            continue
        diff = baseline_value - current_value
        if diff > max_regression:
            passed = False
            warnings.append(
                f"REGRESSION: {key} dropped from {baseline_value:.4f} to "
                f"{current_value:.4f} (diff={diff:.4f} > max={max_regression})"
            )
        elif diff > 0:
            warnings.append(
                f"WARNING: {key} slightly dropped from {baseline_value:.4f} to "
                f"{current_value:.4f} (diff={diff:.4f})"
            )

    return passed, warnings


def update_baseline(metrics: RetrievalMetrics, baseline_path: str):
    """Update baseline file with current metrics."""
    bp = Path(baseline_path)
    bp.parent.mkdir(parents=True, exist_ok=True)

    baseline = {
        "generated_at": time.strftime("%Y-%m-%d"),
        "description": "Auto-updated baseline from retrieval eval",
        "metrics": {
            "recall_at_5": round(metrics.recall_at_5, 4),
            "mrr": round(metrics.mrr, 4),
            "ndcg_at_10": round(metrics.ndcg_at_10, 4),
            "no_answer_accuracy": round(metrics.no_answer_accuracy, 4),
            "latency_p50_ms": round(metrics.latency_p50_ms, 2),
            "latency_p95_ms": round(metrics.latency_p95_ms, 2),
        },
        "total_queries": metrics.total_queries,
    }
    bp.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")


def format_report(
    all_results: dict[str, list[dict]],
    all_metrics: dict[str, RetrievalMetrics],
    baseline_warnings: list[str] | None = None,
) -> str:
    """Format a human-readable Markdown report."""
    lines = [
        "# Retrieval Eval Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Dataset | Queries | Recall@5 | MRR | nDCG@10 | No-Answer Acc | P50 (ms) | P95 (ms) |")
    lines.append("|---------|---------|----------|-----|---------|---------------|----------|----------|")
    for name, m in all_metrics.items():
        lines.append(
            f"| {name} | {m.total_queries} | {m.recall_at_5:.4f} | {m.mrr:.4f} "
            f"| {m.ndcg_at_10:.4f} | {m.no_answer_accuracy:.4f} "
            f"| {m.latency_p50_ms:.1f} | {m.latency_p95_ms:.1f} |"
        )

    # Per-query details
    lines.append("")
    lines.append("## Per-Query Details")
    lines.append("")
    for dataset_name, results in all_results.items():
        lines.append(f"### {dataset_name}")
        lines.append("")
        for r in results:
            status = "PASS" if r["recall"] >= 1.0 else "MISS"
            lines.append(
                f"- [{status}] `{r['query']}` — recall={r['recall']:.2f}, "
                f"mrr={r['mrr']:.2f}, ndcg={r['ndcg']:.2f}, "
                f"latency={r['latency_ms']:.1f}ms"
            )
        lines.append("")

    if baseline_warnings:
        lines.append("## Baseline Comparison")
        lines.append("")
        for w in baseline_warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by `evals/run_retrieval_eval.py`*")
    return "\n".join(lines)


def run_eval(
    datasets: list[str],
    use_fake_embedding: bool = False,
    baseline_path: str | None = None,
    max_regression: float = 0.02,
    update_baseline_flag: bool = False,
) -> tuple[int, dict]:
    """Run retrieval evaluation.

    Returns (exit_code, report_data).
    exit_code: 0 = pass, 1 = regression detected.
    """
    # Build index
    index = build_index(use_fake_embedding=use_fake_embedding)

    all_results: dict[str, list[dict]] = {}
    all_metrics: dict[str, RetrievalMetrics] = {}

    for ds_name in datasets:
        ds_path = DATASETS_DIR / f"{ds_name}.yaml"
        if not ds_path.exists():
            print(f"WARNING: Dataset not found: {ds_path}", file=sys.stderr)
            continue

        items = load_dataset(ds_path)
        query_results = []
        for item in items:
            result = run_single_query(index, item)
            query_results.append(result)

        metrics = aggregate_retrieval_metrics(query_results)
        all_results[ds_name] = query_results
        all_metrics[ds_name] = metrics

    # Baseline comparison
    baseline_warnings = []
    passed = True
    if baseline_path:
        # Aggregate all metrics for baseline comparison
        combined = RetrievalMetrics()
        total_queries = sum(m.total_queries for m in all_metrics.values())
        combined.total_queries = total_queries

        retrieval_metrics = [
            m for m, name in zip(all_metrics.values(), all_metrics.keys())
            if name != "retrieval_no_answer"
        ]
        if retrieval_metrics:
            combined.recall_at_5 = (
                sum(m.recall_at_5 for m in retrieval_metrics) / len(retrieval_metrics)
            )
            combined.mrr = sum(m.mrr for m in retrieval_metrics) / len(retrieval_metrics)
            combined.ndcg_at_10 = (
                sum(m.ndcg_at_10 for m in retrieval_metrics) / len(retrieval_metrics)
            )

        no_answer_metrics = [
            m for m, name in zip(all_metrics.values(), all_metrics.keys())
            if name == "retrieval_no_answer"
        ]
        if no_answer_metrics:
            combined.no_answer_accuracy = no_answer_metrics[0].no_answer_accuracy

        if update_baseline_flag:
            update_baseline(combined, baseline_path)
            baseline_warnings = ["Baseline updated with current metrics."]
        else:
            passed, baseline_warnings = compare_with_baseline(
                combined, baseline_path, max_regression
            )

    report_data = {
        "all_results": {
            name: [
                {k: v for k, v in r.items() if k != "top_result"}
                for r in results
            ]
            for name, results in all_results.items()
        },
        "all_metrics": {
            name: asdict(m) for name, m in all_metrics.items()
        },
        "baseline_warnings": baseline_warnings,
        "passed": passed,
    }

    return 0 if passed else 1, report_data


def main():
    parser = argparse.ArgumentParser(description="Offline retrieval evaluation")
    parser.add_argument("--all", action="store_true", help="Run all retrieval_* datasets")
    parser.add_argument("--dataset", type=str, help="Run a specific dataset (e.g., retrieval_zh)")
    parser.add_argument(
        "--baseline", type=str, default=None,
        help="Path to baseline JSON for comparison"
    )
    parser.add_argument(
        "--max-regression", type=float, default=0.02,
        help="Maximum allowed regression (default: 0.02)"
    )
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="Update baseline file with current metrics"
    )
    parser.add_argument("--output", type=str, help="Save JSON report to file")
    parser.add_argument(
        "--report", choices=["text", "markdown", "json"], default="text",
        help="Report format"
    )
    parser.add_argument(
        "--fake-embedding", action="store_true",
        help="Use deterministic fake embeddings (for CI)"
    )
    args = parser.parse_args()

    if not args.all and not args.dataset:
        parser.error("Please specify --all or --dataset")

    # Determine which datasets to run
    if args.all:
        datasets = [
            p.stem for p in sorted(DATASETS_DIR.glob("retrieval_*.yaml"))
        ]
    else:
        datasets = [args.dataset]

    if not datasets:
        print("ERROR: No retrieval datasets found.", file=sys.stderr)
        sys.exit(1)

    print(f"Running retrieval eval on: {', '.join(datasets)}")
    if args.fake_embedding:
        print("  Mode: fake-embedding (deterministic, for CI)")

    exit_code, report_data = run_eval(
        datasets=datasets,
        use_fake_embedding=args.fake_embedding,
        baseline_path=args.baseline,
        max_regression=args.max_regression,
        update_baseline_flag=args.update_baseline,
    )

    # Output report
    if args.report == "json":
        report_text = json.dumps(report_data, ensure_ascii=False, indent=2)
    elif args.report == "markdown":
        # Reconstruct metrics objects for formatting
        all_metrics_objs = {
            name: RetrievalMetrics(**vals)
            for name, vals in report_data["all_metrics"].items()
        }
        report_text = format_report(
            report_data["all_results"],
            all_metrics_objs,
            report_data.get("baseline_warnings"),
        )
    else:
        # Text summary
        lines = []
        for name, m_dict in report_data["all_metrics"].items():
            m = RetrievalMetrics(**m_dict)
            lines.append(f"\n{'='*60}")
            lines.append(f"Dataset: {name}")
            lines.append(f"{'='*60}")
            lines.append(f"  Queries:      {m.total_queries}")
            lines.append(f"  Recall@5:     {m.recall_at_5:.4f}")
            lines.append(f"  MRR:          {m.mrr:.4f}")
            lines.append(f"  nDCG@10:      {m.ndcg_at_10:.4f}")
            lines.append(f"  No-Answer:    {m.no_answer_accuracy:.4f}")
            lines.append(f"  Latency P50:  {m.latency_p50_ms:.1f}ms")
            lines.append(f"  Latency P95:  {m.latency_p95_ms:.1f}ms")

        if report_data.get("baseline_warnings"):
            lines.append(f"\nBaseline Comparison:")
            for w in report_data["baseline_warnings"]:
                lines.append(f"  - {w}")

        lines.append(f"\nOverall: {'PASS' if report_data['passed'] else 'FAIL'}")
        report_text = "\n".join(lines)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report_text, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(report_text)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
