"""知识演进评测脚本冒烟测试(Phase 6 T6.4)。"""
from evals.run_knowledge_evolution_eval import run_eval


def test_knowledge_evolution_eval_overall_pass():
    report = run_eval()
    assert report.overall_pass, {
        m.name: (m.value, m.passed, m.detail) for m in report.metrics
    }
    names = {m.name for m in report.metrics}
    for required in (
        "claim_provenance_completeness",
        "evidence_location_completeness",
        "cross_source_merge_accuracy",
        "update_propagation_recall",
        "unsupported_claim_detection",
        "page_identity_stability",
        "migration_page_parity",
        "projection_parity",
    ):
        assert required in names
