"""Validate eval datasets and fixtures.

These tests ensure that the retrieval evaluation datasets are well-formed:
- All referenced fixtures exist on disk
- Every retrieval dataset entry has valid expected_sources
- The no_answer dataset has empty expected_sources
- No duplicate queries across datasets
- Each block_contains string actually appears in the referenced fixture
"""
from pathlib import Path

import yaml

EVALS_DIR = Path(__file__).parent.parent / "evals"
FIXTURES_DIR = EVALS_DIR / "fixtures"
DATASETS_DIR = EVALS_DIR / "datasets"


def test_fixtures_exist():
    """All referenced fixtures must exist on disk."""
    fixtures = list(FIXTURES_DIR.glob("*"))
    assert len(fixtures) >= 6


def test_dataset_has_expected_sources():
    """Every retrieval dataset entry must have non-empty expected_sources (except no_answer)."""
    for yaml_file in DATASETS_DIR.glob("retrieval_*.yaml"):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        for item in data:
            assert "query" in item
            sources = item.get("expected_sources", [])
            if item.get("category") == "no_answer":
                continue
            assert len(sources) > 0, f"Empty expected_sources in {yaml_file.name}: {item['query']}"
            for src in sources:
                assert "path" in src
                fixture_path = EVALS_DIR / src["path"]
                assert fixture_path.exists(), f"Fixture not found: {src['path']}"


def test_no_answer_dataset_has_empty_sources():
    """no_answer dataset must have empty expected_sources."""
    data = yaml.safe_load(
        (DATASETS_DIR / "retrieval_no_answer.yaml").read_text(encoding="utf-8")
    )
    for item in data:
        assert item.get("expected_sources", []) == [], (
            f"no_answer should have empty sources: {item['query']}"
        )


def test_no_duplicate_queries():
    """No duplicate queries across retrieval datasets."""
    all_queries = set()
    for yaml_file in DATASETS_DIR.glob("retrieval_*.yaml"):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        for item in data:
            q = item["query"]
            assert q not in all_queries, f"Duplicate query: {q}"
            all_queries.add(q)


def test_block_contains_matches_fixture():
    """Each block_contains string must appear in the referenced fixture file."""
    for yaml_file in DATASETS_DIR.glob("retrieval_*.yaml"):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        for item in data:
            for src in item.get("expected_sources", []):
                fixture_path = EVALS_DIR / src["path"]
                content = fixture_path.read_text(encoding="utf-8")
                assert src["block_contains"] in content, (
                    f"'{src['block_contains']}' not found in {src['path']} "
                    f"for query: {item['query']}"
                )
