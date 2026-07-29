"""Golden V2 schema, review, freeze, and formal-run gates."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2.0"
ALLOWED_SPLITS = frozenset({"development", "regression", "validation", "holdout"})
ALLOWED_ANSWERABILITY = frozenset(
    {"answerable", "no_answer", "clarification_required"}
)
ALLOWED_RISK = frozenset({"P0", "P1", "P2", "P3"})
ALLOWED_SOURCE_ROLES = frozenset(
    {"primary", "supporting", "acceptable", "forbidden"}
)
ALLOWED_MATCH = frozenset({"exact", "normalized", "numeric_unit", "semantic_review"})
ALLOWED_AMBIGUITY = frozenset({"clear", "needs_clarification", "disputed"})
ALLOWED_ANNOTATION = frozenset({"candidate", "human_reviewed"})

# Cases already exposed via multi-round tuning of the V1 golden set.
LEGACY_V1_EXPOSED_CASE_IDS = frozenset(
    f"KB-{i:03d}" for i in range(1, 38)
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dataset_hash(rows: list[dict[str, Any]]) -> str:
    raw = json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256_text(raw)


def corpus_snapshot_token(db_path: Path) -> str:
    return f"kb.db:{sha256_file(db_path)[:16]}"


def _is_iso8601(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate_case_schema(row: dict[str, Any]) -> list[str]:
    """Return field error codes for a single Golden V2 case."""
    errors: list[str] = []
    if not str(row.get("case_id") or "").strip():
        errors.append("case_id")
    if str(row.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("schema_version")
    if row.get("split") not in ALLOWED_SPLITS:
        errors.append("split")
    if not str(row.get("category") or "").strip():
        errors.append("category")
    if row.get("risk_level") not in ALLOWED_RISK:
        errors.append("risk_level")
    if not str(row.get("query") or "").strip():
        errors.append("query")
    if row.get("answerability") not in ALLOWED_ANSWERABILITY:
        errors.append("answerability")
    if row.get("annotation_source") not in ALLOWED_ANNOTATION:
        errors.append("annotation_source")

    intent = row.get("intent")
    if intent is not None and not str(intent).strip():
        errors.append("intent")

    sources = row.get("expected_sources")
    if sources is None:
        sources = []
    if not isinstance(sources, list):
        errors.append("expected_sources")
        sources = []
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            errors.append(f"expected_sources[{i}]")
            continue
        if not str(src.get("knowledge_id") or "").strip():
            errors.append(f"expected_sources[{i}].knowledge_id")
        role = src.get("source_role")
        if role is not None and role not in ALLOWED_SOURCE_ROLES:
            errors.append(f"expected_sources[{i}].source_role")
        pid = str(src.get("passage_id") or "").strip()
        missing_reason = str(src.get("passage_missing_reason") or "").strip()
        if not pid and not missing_reason:
            errors.append(f"expected_sources[{i}].passage_id_or_reason")

    groups = row.get("required_fact_groups")
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        errors.append("required_fact_groups")
        groups = []
    for i, g in enumerate(groups):
        if not isinstance(g, dict):
            errors.append(f"required_fact_groups[{i}]")
            continue
        if not str(g.get("fact_id") or "").strip():
            errors.append(f"required_fact_groups[{i}].fact_id")
        mp = g.get("match_policy")
        if mp is not None and mp not in ALLOWED_MATCH:
            errors.append(f"required_fact_groups[{i}].match_policy")

    amb = row.get("ambiguity")
    if amb is not None:
        if not isinstance(amb, dict):
            errors.append("ambiguity")
        elif amb.get("status") not in ALLOWED_AMBIGUITY:
            errors.append("ambiguity.status")

    return errors


def _source_review_key(src: dict[str, Any], index: int) -> str:
    kid = str(src.get("knowledge_id") or "").strip()
    pid = str(src.get("passage_id") or "").strip()
    role = str(src.get("source_role") or "primary").strip()
    return f"source:{index}:{kid}:{pid}:{role}"


def _fact_review_key(group: dict[str, Any], index: int) -> str:
    fid = str(group.get("fact_id") or f"f{index}").strip()
    epid = str(
        group.get("evidence_passage_id") or group.get("passage_id") or ""
    ).strip()
    return f"fact:{fid}:{epid}"


def _evidence_checked_entries(review: dict[str, Any]) -> list[Any]:
    raw = review.get("evidence_checked")
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return []


def _evidence_checked_covers(entries: list[Any], key: str) -> bool:
    """True when evidence_checked records a decision for the given source/fact key."""
    for entry in entries:
        if isinstance(entry, str) and entry == key:
            return True
        if not isinstance(entry, dict):
            continue
        if str(entry.get("key") or "").strip() == key:
            return True
        # Allow structured records keyed by source/fact identifiers
        etype = str(entry.get("type") or entry.get("kind") or "").strip()
        if etype in {"source", "expected_source"}:
            kid = str(entry.get("knowledge_id") or "").strip()
            pid = str(entry.get("passage_id") or "").strip()
            role = str(entry.get("source_role") or entry.get("role") or "").strip()
            if kid and f":{kid}:" in key and (not pid or pid in key):
                if not role or role in key:
                    decision = entry.get("decision") or entry.get("status")
                    if decision is None or str(decision).strip():
                        return True
        if etype in {"fact", "required_fact", "fact_group"}:
            fid = str(entry.get("fact_id") or "").strip()
            if fid and f"fact:{fid}:" in key:
                decision = entry.get("decision") or entry.get("status")
                if decision is None or str(decision).strip():
                    return True
    return False


def validate_review_for_freeze(row: dict[str, Any]) -> list[str]:
    """Strict dual-review freeze gate (no fabricated reviewers)."""
    errors: list[str] = []
    if row.get("annotation_source") != "human_reviewed":
        errors.append("annotation_source")

    review = row.get("review")
    if not isinstance(review, dict):
        return errors + ["review"]

    status = str(review.get("status") or "").strip()
    if status in {"needs_adjudication", "disputed", "rejected"}:
        errors.append("review.status_not_approved")
    elif status != "approved":
        errors.append("review.status_not_approved")

    primary = str(review.get("primary_reviewer") or "").strip()
    secondary = str(review.get("secondary_reviewer") or "").strip()
    if not primary:
        errors.append("review.primary_reviewer")
    if not secondary:
        errors.append("review.secondary_reviewer")
    if primary and secondary and primary == secondary:
        errors.append("review.reviewers_must_differ")
    for field in ("primary_reviewed_at", "secondary_reviewed_at"):
        if not _is_iso8601(review.get(field)):
            errors.append(f"review.{field}")

    # evidence_checked is required for freeze (Task 2.0.4).
    checked = _evidence_checked_entries(review)
    if not checked:
        errors.append("review.evidence_checked")

    needs_adj = bool(review.get("disagreement")) or status in {
        "needs_adjudication",
        "disputed",
    }
    adjudicator = str(review.get("adjudicator") or "").strip()
    if needs_adj:
        if not adjudicator or not _is_iso8601(review.get("adjudicated_at")):
            errors.append("review.adjudication_incomplete")
        else:
            # Adjudicator must be independent of both reviewers.
            if adjudicator and adjudicator in {primary, secondary}:
                errors.append("review.adjudicator_must_differ")
            # Record of original disagreement is required when adjudicating.
            if not str(
                review.get("disagreement_summary")
                or review.get("original_disagreement")
                or review.get("decision_notes")
                or ""
            ).strip():
                errors.append("review.adjudication_record_missing")

    amb = row.get("ambiguity") or {}
    if isinstance(amb, dict) and amb.get("status") == "disputed":
        errors.append("ambiguity.disputed")

    answerability = row.get("answerability")
    sources = row.get("expected_sources") or []
    if answerability == "answerable":
        if not sources:
            errors.append("expected_sources")
        for i, src in enumerate(sources):
            if not isinstance(src, dict):
                continue
            role = src.get("source_role") or "primary"
            if role in {"primary", "supporting"}:
                if not str(src.get("passage_id") or "").strip():
                    errors.append(f"expected_sources[{i}].passage_id_required_for_freeze")
            # Every expected/acceptable/forbidden source needs a review decision.
            if role in {"primary", "supporting", "acceptable", "forbidden"}:
                skey = _source_review_key(src, i)
                if checked and not _evidence_checked_covers(checked, skey):
                    # Also accept a generic per-knowledge_id decision entry.
                    kid = str(src.get("knowledge_id") or "").strip()
                    generic_ok = any(
                        isinstance(e, dict)
                        and str(e.get("knowledge_id") or "").strip() == kid
                        and str(e.get("decision") or e.get("status") or "").strip()
                        for e in checked
                    ) if kid else False
                    if not generic_ok:
                        errors.append(f"review.evidence_checked.missing_source[{i}]")
        groups = row.get("required_fact_groups") or []
        if not groups:
            errors.append("required_fact_groups")
        for i, g in enumerate(groups):
            if not isinstance(g, dict):
                continue
            if not (
                str(g.get("evidence_passage_id") or "").strip()
                or str(g.get("passage_id") or "").strip()
            ):
                errors.append(f"required_fact_groups[{i}].evidence_passage_id")
            fkey = _fact_review_key(g, i)
            if checked and not _evidence_checked_covers(checked, fkey):
                fid = str(g.get("fact_id") or "").strip()
                generic_ok = any(
                    isinstance(e, dict)
                    and str(e.get("fact_id") or "").strip() == fid
                    and (
                        str(e.get("decision") or e.get("status") or "").strip()
                        or e.get("passage_checked") is True
                        or e.get("evidence_checked") is True
                    )
                    for e in checked
                ) if fid else False
                if not generic_ok:
                    errors.append(f"review.evidence_checked.missing_fact[{i}]")
    elif answerability == "no_answer":
        reason = str(
            row.get("no_answer_reason")
            or (row.get("ambiguity") or {}).get("reason")
            or row.get("reason")
            or ""
        ).strip()
        if not reason:
            errors.append("no_answer.reason")

    corpus = row.get("corpus_snapshot") or {}
    if not isinstance(corpus, dict) or not str(
        corpus.get("sha") or corpus.get("corpus_snapshot_sha") or ""
    ).strip():
        errors.append("corpus_snapshot")

    return errors


def validate_freeze_row(
    row: dict[str, Any],
    *,
    expected_corpus_sha: str | None = None,
) -> list[str]:
    errors = validate_case_schema(row)
    errors.extend(validate_review_for_freeze(row))
    if expected_corpus_sha:
        corpus = row.get("corpus_snapshot") or {}
        got = str(
            corpus.get("sha")
            or corpus.get("corpus_snapshot_sha")
            or row.get("corpus_snapshot_sha")
            or ""
        )
        if got != expected_corpus_sha:
            errors.append("corpus_snapshot_sha_mismatch")
    # De-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def assert_split_isolation(rows: list[dict[str, Any]]) -> list[str]:
    """Return isolation violations across splits."""
    errors: list[str] = []
    by_split: dict[str, set[str]] = {}
    for row in rows:
        cid = str(row.get("case_id") or "")
        split = str(row.get("split") or "")
        by_split.setdefault(split, set()).add(cid)
        if split == "holdout" and cid in LEGACY_V1_EXPOSED_CASE_IDS:
            errors.append(f"holdout_contains_v1_exposed:{cid}")
        if cid in LEGACY_V1_EXPOSED_CASE_IDS and split not in {
            "development",
            "regression",
        }:
            errors.append(f"v1_exposed_must_be_dev_or_regression:{cid}:{split}")

    dev = by_split.get("development", set()) | by_split.get("regression", set())
    holdout = by_split.get("holdout", set())
    overlap = dev & holdout
    for cid in sorted(overlap):
        errors.append(f"development_holdout_overlap:{cid}")
    return errors


def is_frozen_path(path: Path) -> bool:
    parts = {p.lower() for p in path.resolve().parts}
    return "frozen" in parts


def is_candidates_path(path: Path) -> bool:
    parts = {p.lower() for p in path.resolve().parts}
    return "candidates" in parts


def is_reviewed_path(path: Path) -> bool:
    parts = {p.lower() for p in path.resolve().parts}
    return "reviewed" in parts


def validate_formal_golden_path(golden_path: Path) -> list[str]:
    """Formal harness may only read frozen V2 data (path-level gate)."""
    errors: list[str] = []
    path = Path(golden_path)
    if not path.exists():
        return ["golden_missing"]
    if is_candidates_path(path):
        errors.append("formal_rejects_candidates")
    if is_reviewed_path(path) and not is_frozen_path(path):
        errors.append("formal_rejects_unfrozen_reviewed")
    if not is_frozen_path(path):
        errors.append("formal_requires_frozen_path")
    return errors


def row_corpus_sha(row: dict[str, Any]) -> str:
    corpus = row.get("corpus_snapshot") or {}
    if isinstance(corpus, dict):
        return str(
            corpus.get("sha")
            or corpus.get("corpus_snapshot_sha")
            or ""
        ).strip()
    return str(row.get("corpus_snapshot_sha") or "").strip()


def build_review_manifest_hash(
    rows: list[dict[str, Any]],
    *,
    dataset_hash_value: str,
    schema_hash_value: str,
) -> str:
    """Deterministic review manifest from audit metadata + dataset/schema hashes.

    Does not invent reviewers — only hashes what is already on the rows.
    """
    entries: list[dict[str, Any]] = []
    for row in rows:
        review = row.get("review") if isinstance(row.get("review"), dict) else {}
        entries.append(
            {
                "case_id": row.get("case_id"),
                "annotation_source": row.get("annotation_source"),
                "primary_reviewer": review.get("primary_reviewer"),
                "secondary_reviewer": review.get("secondary_reviewer"),
                "adjudicator": review.get("adjudicator"),
                "primary_reviewed_at": review.get("primary_reviewed_at"),
                "secondary_reviewed_at": review.get("secondary_reviewed_at"),
                "adjudicated_at": review.get("adjudicated_at"),
                "status": review.get("status"),
                "disagreement": review.get("disagreement"),
                "corpus_sha": row_corpus_sha(row),
            }
        )
    entries.sort(key=lambda e: str(e.get("case_id") or ""))
    payload = {
        "dataset_hash": dataset_hash_value,
        "schema_hash": schema_hash_value,
        "reviews": entries,
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256_text(raw)


def validate_formal_frozen_dataset(
    golden_path: Path,
    *,
    expected_corpus_sha: str | None = None,
    db_path: Path | None = None,
    schema_hash: str = "",
) -> tuple[list[str], dict[str, str]]:
    """True formal freeze validation — path name alone is never sufficient.

    Returns (errors, meta) where meta includes:
      review_manifest_hash, corpus_snapshot, dataset_hash, split
    """
    errors = validate_formal_golden_path(golden_path)
    meta: dict[str, str] = {
        "review_manifest_hash": "",
        "corpus_snapshot": "",
        "dataset_hash": "",
        "split": "",
    }
    path = Path(golden_path)
    if "golden_missing" in errors:
        return errors, meta

    rows = load_jsonl(path)
    if not rows:
        errors.append("formal_frozen_empty")
        return errors, meta

    corpus_sha = expected_corpus_sha
    if not corpus_sha and db_path is not None and Path(db_path).exists():
        corpus_sha = corpus_snapshot_token(Path(db_path))

    splits: set[str] = set()
    corpus_values: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"row[{i}].not_object")
            continue
        row_errors = validate_freeze_row(row, expected_corpus_sha=corpus_sha)
        for err in row_errors:
            errors.append(f"row[{i}].{row.get('case_id') or '?'}:{err}")
        if row.get("annotation_source") != "human_reviewed":
            # Already covered by validate_freeze_row, keep explicit for clarity.
            pass
        split = str(row.get("split") or "").strip()
        if split:
            splits.add(split)
        csha = row_corpus_sha(row)
        if csha:
            corpus_values.add(csha)

    if len(splits) > 1:
        errors.append("formal_mixed_split")
    if len(splits) == 1:
        meta["split"] = next(iter(splits))
    elif not splits:
        errors.append("formal_split_missing")

    if len(corpus_values) > 1:
        errors.append("formal_corpus_inconsistent")
    if len(corpus_values) == 1:
        meta["corpus_snapshot"] = next(iter(corpus_values))
    elif corpus_sha:
        meta["corpus_snapshot"] = corpus_sha

    if corpus_sha and meta["corpus_snapshot"] and meta["corpus_snapshot"] != corpus_sha:
        errors.append("formal_corpus_db_mismatch")

    ds_hash = dataset_hash(rows)
    meta["dataset_hash"] = ds_hash
    review_hash = build_review_manifest_hash(
        rows,
        dataset_hash_value=ds_hash,
        schema_hash_value=schema_hash or "",
    )
    if not review_hash:
        errors.append("formal_review_manifest_empty")
    else:
        meta["review_manifest_hash"] = review_hash

    # De-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out, meta


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def scorer_contract_hash(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parents[2]
    paths = [
        root / "evals" / "hit_rate_v2" / "scoring.py",
        root / "evals" / "hit_rate_v2" / "models.py",
        root / "evals" / "hit_rate_v2" / "validation.py",
    ]
    digest = hashlib.sha256()
    for p in paths:
        if p.exists():
            digest.update(p.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
