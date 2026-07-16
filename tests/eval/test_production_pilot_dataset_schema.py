"""Schema & policy gates for production pilot human-grounded datasets."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent / "datasets"

RETRIEVAL = DATA / "production_pilot_retrieval.jsonl"
NO_ANSWER = DATA / "production_pilot_no_answer.jsonl"
NUMERIC = DATA / "production_pilot_numeric_units.jsonl"
ROUTING = DATA / "production_pilot_routing.jsonl"
ANSWERS = DATA / "production_pilot_answer_citations.jsonl"


def _load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_dataset_files_exist() -> None:
    for p in (RETRIEVAL, NO_ANSWER, NUMERIC, ROUTING, ANSWERS):
        assert p.is_file(), f"missing {p}"


def test_minimum_counts() -> None:
    assert len(_load(RETRIEVAL)) >= 60
    assert len(_load(NO_ANSWER)) >= 30
    assert len(_load(NUMERIC)) >= 25
    assert len(_load(ROUTING)) >= 40
    assert len(_load(ANSWERS)) >= 25


def test_retrieval_requires_non_empty_expected_ids() -> None:
    for row in _load(RETRIEVAL):
        assert row.get("annotation_source") == "human"
        assert row.get("expected_ids"), f"{row.get('id')} empty expected_ids"
        assert row.get("category") in {
            "keyword",
            "semantic",
            "synonym",
            "multi_constraint",
            "long_query",
        }
        assert "PAD" not in str(row.get("id", "")).upper()
        assert row.get("category") != "pad"
        assert "hit_or_empty" not in str(row.get("annotator_notes", "")).lower()


def test_no_answer_schema() -> None:
    for row in _load(NO_ANSWER):
        assert row.get("expected_no_answer") is True
        assert row.get("reason")
        assert row.get("annotation_source") == "human"


def test_numeric_schema_and_no_empty_pass_for_hit() -> None:
    for row in _load(NUMERIC):
        assert row.get("annotation_source") == "human"
        if row.get("expected_no_answer"):
            continue
        # Hit samples must have ground truth docs or units
        assert row.get("expected_ids") or row.get("expected_units"), row.get("id")


def test_routing_schema() -> None:
    for row in _load(ROUTING):
        assert row.get("expected_mode") in {"structured", "graph", "hybrid"}
        assert row.get("expected_tool")
        assert row.get("expected_task_outcome") in {
            "non_empty",
            "no_answer",
            "validation_error",
            "graph_result",
            "structured_result",
        }


def test_answer_citation_schema() -> None:
    for row in _load(ANSWERS):
        assert row.get("question")
        facts = row.get("expected_answer_facts") or []
        assert facts, row.get("id")
        for f in facts:
            assert f.get("statement")
            assert f.get("supporting_knowledge_ids"), f"{row.get('id')} fact without support"
        assert int(row.get("minimum_sources") or 0) >= 1


def test_no_pad_samples_in_scoring_datasets() -> None:
    for path in (RETRIEVAL, NO_ANSWER, NUMERIC, ROUTING, ANSWERS):
        for row in _load(path):
            rid = str(row.get("id", "")).upper()
            assert not rid.startswith("PAD"), rid
            assert row.get("category") != "pad"
            assert row.get("type") != "pad"
