"""Prevent historical release notes from being treated as current evidence."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINAL_REVIEW = ROOT / "docs" / "superpowers" / "reviews" / "verified-hybrid-final-review.md"
PROGRESS = ROOT / "PROGRESS.md"
BASELINE = ROOT / "docs" / "superpowers" / "reviews" / "verified-hybrid-correction-baseline.md"


def test_current_final_review_requires_complete_release_evidence() -> None:
    """An active final review cannot rely on an old deterministic eval alone."""
    text = FINAL_REVIEW.read_text(encoding="utf-8")
    if "Historical / Superseded by correction" in text:
        return

    required_evidence = (
        "Ruff: 0",
        "mypy: 0",
        "Python 3.10",
        "Docker",
        "Windows",
        "真实 Hybrid A/B",
    )
    missing = [item for item in required_evidence if item not in text]
    assert not missing, f"当前最终评审缺少发布门禁证据: {missing}"


def test_progress_records_verified_hybrid_correction_completion() -> None:
    """PROGRESS must record completion only with the corrected plan references."""
    text = PROGRESS.read_text(encoding="utf-8")
    assert "Verified Hybrid 融合收束纠偏已完成" in text
    assert "远端 CI 全绿" in text
    assert "2026-07-13-verified-hybrid-convergence-correction-design.md" in text
    assert "2026-07-13-verified-hybrid-convergence-correction.md" in text


def test_baseline_records_current_unmet_release_gates() -> None:
    """The correction baseline is reviewable evidence, not an implicit pass."""
    text = BASELINE.read_text(encoding="utf-8")
    for required in ("Ruff", "mypy", "真实 Hybrid A/B", "未通过", "1646"):
        assert required in text, f"baseline 缺少关键证据: {required}"
