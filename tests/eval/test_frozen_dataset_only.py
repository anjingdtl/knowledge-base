from __future__ import annotations

from pathlib import Path

from scripts import production_pilot_mcp_harness

ROOT = Path(__file__).resolve().parents[2]


def test_formal_harness_reads_only_frozen_directory() -> None:
    assert production_pilot_mcp_harness.DATA == ROOT / "tests" / "eval" / "datasets" / "frozen"


def test_all_five_frozen_dataset_paths_exist() -> None:
    frozen = ROOT / "tests" / "eval" / "datasets" / "frozen"
    expected = {
        "production_pilot_retrieval.jsonl",
        "production_pilot_no_answer.jsonl",
        "production_pilot_numeric_units.jsonl",
        "production_pilot_routing.jsonl",
        "production_pilot_answer_citations.jsonl",
    }
    assert expected <= {path.name for path in frozen.glob("*.jsonl")}

