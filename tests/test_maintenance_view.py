"""维护中心向量覆盖率修复的 GUI 回归测试。"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def test_vector_coverage_worker_reports_progress_and_result(monkeypatch, qapp):
    from src.gui.maintenance_view import VectorCoverageRepairWorker

    progress = []
    results = []

    def fake_repair(progress_callback):
        progress_callback(2, 3)
        return {
            "total_blocks": 3,
            "missing_before": 3,
            "repaired": 3,
            "failed": 0,
            "coverage_before": 0.0,
            "coverage_after": 1.0,
            "errors": [],
        }

    monkeypatch.setattr("src.gui.maintenance_view.repair_missing_block_vectors", fake_repair)
    worker = VectorCoverageRepairWorker()
    worker.progress.connect(lambda current, total: progress.append((current, total)))
    worker.finished_ok.connect(results.append)

    worker.run()

    assert progress == [(2, 3)]
    assert results[0]["coverage_after"] == 1.0


def test_maintenance_view_shows_current_vector_coverage(monkeypatch, qapp):
    monkeypatch.setattr(
        "src.gui.maintenance_view.get_vector_coverage",
        lambda: {
            "total_blocks": 12,
            "covered_blocks": 9,
            "missing_blocks": 3,
            "coverage": 0.75,
        },
    )
    from src.gui.maintenance_view import MaintenanceView

    view = MaintenanceView()

    assert view.lbl_vector_coverage.text() == "向量覆盖率: 75.0% (9/12)"
    assert view.btn_repair_vectors.text() == "修复向量覆盖率"
