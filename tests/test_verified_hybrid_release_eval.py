from evals.run_verified_hybrid_release_eval import run
from evals.verified_hybrid_release.dataset import build_cases, validate_cases


def test_release_dataset_meets_minimum_contract():
    cases = build_cases()
    assert not validate_cases(cases)
    assert len(cases) >= 60


def test_real_service_release_ab_has_verified_claims():
    report = run()
    assert report["overall_pass"] is True
    assert report["verified_claim_count"] > 0
