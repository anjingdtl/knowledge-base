"""Canonical Wiki V2 Claim 语义真实模型评测(非阻断,Phase 3.5 / C2)。

用真实 embedding 跑 evals/wiki_v2/claim_matching.jsonl 黄金集,输出 action
confusion matrix + 各 action precision。CI 不跑(需 embedding API key),
手动或定时运行验证语义准确率。

Usage:
    python evals/run_wiki_v2_semantic_eval.py            # 人类可读
    python evals/run_wiki_v2_semantic_eval.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
EVAL_DIR = PROJECT_ROOT / "evals" / "wiki_v2"

from src.models.wiki_v2 import (  # noqa: E402
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    normalize_statement,
)
from src.services.wiki_claim_matcher import ClaimMatcher  # noqa: E402

NOW = "2026-07-08T12:00:00+08:00"
ACTIONS = ("new", "supports", "refines", "contradicts", "supersedes", "duplicate", "unresolved")


def load_cases() -> list[dict]:
    path = EVAL_DIR / "claim_matching.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_claim(d: dict, claim_id: str) -> Claim:
    stance = EvidenceStance(d.get("stance", "supports"))
    return Claim(
        schema_version=1, claim_id=claim_id,
        statement=d["s"], normalized_statement=normalize_statement(d["s"]),
        claim_type="fact", status=ClaimStatus.ACTIVE, confidence=0.9,
        valid_from=d.get("vf"), valid_to=d.get("vt"),
        subject_refs=list(d.get("sub", [])), predicate=d.get("pred", ""),
        object_refs=list(d.get("obj", [])),
        evidence=[Evidence(evidence_id="ev_x", stance=stance, knowledge_id="k1", block_id="b1")],
        relations=[], created_at=NOW, updated_at=NOW, revision=1,
    )


def run_eval(embedding) -> dict:
    """用真实 embedding 跑黄金集,返回 confusion matrix + per-action precision。"""
    matcher = ClaimMatcher(embedding=embedding)
    cases = load_cases()
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    llm_or_embed_calls = 0
    for case in cases:
        new = build_claim(case["new"], "new")
        candidates = [build_claim(c, c["id"]) for c in case["candidates"]]
        # 不注入 scores → matcher 用 embedding 自己算(_embed_scores)
        decision = matcher.match(new, candidates)
        llm_or_embed_calls += 1 + len(candidates)
        confusion[case["expected_action"]][decision.action] += 1

    per_action: dict[str, dict] = {}
    for action in ACTIONS:
        tp = confusion[action][action]
        fp = sum(confusion[exp][action] for exp in confusion if exp != action)
        fn = sum(confusion[action][act] for act in confusion[action] if act != action)
        prec = round(tp / (tp + fp), 4) if (tp + fp) else None
        per_action[action] = {"precision": prec, "tp": tp, "fp": fp, "fn": fn}

    return {
        "cases": len(cases),
        "embed_calls": llm_or_embed_calls,
        "confusion_matrix": {exp: dict(acts) for exp, acts in confusion.items()},
        "per_action": per_action,
    }


def print_report(report: dict) -> None:
    print(f"cases: {report['cases']}, embed_calls: {report['embed_calls']}\n")
    print("per-action precision:")
    for action in ACTIONS:
        m = report["per_action"][action]
        prec = m["precision"]
        prec_s = f"{prec:.2%}" if prec is not None else "n/a"
        print(f"  {action:12s} precision={prec_s}  tp={m['tp']} fp={m['fp']} fn={m['fn']}")
    print("\nconfusion matrix (rows=expected, cols=actual):")
    print("  " + " ".join(f"{a:>12s}" for a in ACTIONS))
    for exp in ACTIONS:
        row = report["confusion_matrix"].get(exp, {})
        print(f"  {exp:12s} " + " ".join(f"{row.get(a, 0):>12d}" for a in ACTIONS))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wiki V2 Claim semantic eval (real embedding)")
    parser.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    args = parser.parse_args(argv)

    try:
        from src.core.container import create_container
        container = create_container()
        embedding = container.embedding
    except Exception as exc:  # noqa: BLE001
        print(f"无法初始化 embedding 服务(配 SHINEHE_EMBEDDING_API_KEY): {exc}", file=sys.stderr)
        return 1

    report = run_eval(embedding)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
