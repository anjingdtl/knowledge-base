"""Compound numeric units (珠/米 vs 米) ranking parity."""
from __future__ import annotations

from src.services.numeric_unit_match import (
    apply_numeric_unit_ranking,
    extract_number_units,
    score_numeric_unit_match,
)


def test_extract_beads_per_meter_as_compound_unit() -> None:
    hits = extract_number_units("规格 60珠/米 灯带")
    units = {h.unit for h in hits}
    assert "珠/米" in units or any("/" in u for u in units)


def test_query_meters_demotes_beads_per_meter() -> None:
    r = score_numeric_unit_match("60 米", "规格 60珠/米 灯带")
    assert r["features"]["number_match_unit_mismatch"] is True
    assert r["score_delta"] < 0


def test_query_meters_boosts_plain_meters() -> None:
    r = score_numeric_unit_match("60 米", "长度 60米 光纤")
    assert r["features"]["exact_number_unit_match"] is True
    assert r["score_delta"] > 0


def test_apply_ranking_puts_plain_meters_first() -> None:
    items = [
        {"title": "灯带", "text": "60珠/米", "score": 0.5},
        {"title": "光纤", "text": "60米长度", "score": 0.5},
    ]
    out = apply_numeric_unit_ranking("60 米", items)
    assert "米" in (out[0].get("text") or "") and "珠" not in (out[0].get("text") or "")
