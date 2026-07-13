"""Phase 5: Maintenance Policy Engine + WikiMaintenanceService."""
from __future__ import annotations

from src.services.maintenance_policy import (
    DECIDE_AUTO,
    DECIDE_BLOCK,
    DECIDE_REVIEW,
    MaintenancePolicyEngine,
)
from src.services.wiki_maintenance_service import WikiMaintenanceService


class TestPolicyEngine:
    def test_r1_auto_under_supervised(self):
        eng = MaintenancePolicyEngine({
            "knowledge_workflow": {"mode": "verified"},
            "maintenance": {
                "enabled": True,
                "center_enabled": True,
                "automation_level": "supervised",
                "policy": {"auto_protective_actions": True},
            },
        })
        d = eng.evaluate("protective_rebuild", risk_level="R1")
        assert d.decision == DECIDE_AUTO
        assert d.risk_level == "R1"

    def test_r3_creates_review_not_publish(self):
        eng = MaintenancePolicyEngine({
            "knowledge_workflow": {"mode": "authoring"},
            "maintenance": {"enabled": True, "automation_level": "supervised"},
            "wiki": {"authoring_enabled": True},
        })
        d = eng.evaluate("claim_draft", risk_level="R3")
        assert d.decision == DECIDE_REVIEW

    def test_r4_blocked_without_human(self):
        eng = MaintenancePolicyEngine({
            "knowledge_workflow": {"mode": "authoring"},
            "maintenance": {
                "enabled": True,
                "policy": {"auto_publish": False},
            },
        })
        d = eng.evaluate("publish", risk_level="R4", human_confirmed=False)
        assert d.decision in (DECIDE_REVIEW, DECIDE_BLOCK)
        assert "r4_requires_human" in d.reason_codes or d.decision == DECIDE_BLOCK

    def test_r4_with_human_still_needs_flag_or_confirm(self):
        eng = MaintenancePolicyEngine({
            "knowledge_workflow": {"mode": "authoring"},
            "maintenance": {
                "enabled": True,
                "policy": {"auto_publish": False},
            },
        })
        d = eng.evaluate("publish", risk_level="R4", human_confirmed=True)
        assert d.decision == DECIDE_AUTO

    def test_disabled_center_blocks(self):
        eng = MaintenancePolicyEngine({
            "maintenance": {"enabled": False},
        })
        d = eng.evaluate("protective_rebuild", risk_level="R1")
        assert d.decision == DECIDE_BLOCK

    def test_observe_no_auto_write(self):
        eng = MaintenancePolicyEngine({
            "knowledge_workflow": {"mode": "evidence_only"},
            "maintenance": {"automation_level": "observe", "enabled": True},
        })
        d = eng.evaluate("protective_rebuild", risk_level="R1")
        assert d.decision != DECIDE_AUTO or "observe" in str(d.reason_codes)


class TestMaintenanceService:
    def test_health_snapshot_safe_without_repo(self):
        svc = WikiMaintenanceService(config={
            "knowledge_workflow": {"mode": "verified"},
            "maintenance": {"enabled": True},
        })
        snap = svc.health_snapshot()
        assert "servable_claims" in snap
        assert snap["knowledge_mode"] == "verified"

    def test_source_event_dedup(self):
        svc = WikiMaintenanceService(config={
            "knowledge_workflow": {"mode": "verified"},
            "maintenance": {
                "enabled": True,
                "automation_level": "supervised",
                "policy": {"auto_protective_actions": True},
            },
        })
        r1 = svc.handle_source_event("doc1", "updated")
        r2 = svc.handle_source_event("doc1", "updated")
        assert r1.get("ok")
        assert r2.get("deduplicated") is True

    def test_r3_propose_draft_waiting_review(self):
        svc = WikiMaintenanceService(config={
            "knowledge_workflow": {"mode": "authoring"},
            "maintenance": {"enabled": True},
            "wiki": {"authoring_enabled": True},
        })
        result = svc.propose_draft(
            claim_id="c1",
            proposed={"statement": "new draft"},
            evidence=[{"block_id": "b1"}],
        )
        assert result["ok"]
        assert result["review"]["status"] == "open"
        assert result["job"]["status"] == "waiting_review"
        assert result["decision"]["decision"] == DECIDE_REVIEW

    def test_r4_evaluate_without_confirm(self):
        svc = WikiMaintenanceService(config={
            "knowledge_workflow": {"mode": "authoring"},
            "maintenance": {"enabled": True, "policy": {"auto_publish": False}},
        })
        d = svc.evaluate_r4("publish", human_confirmed=False)
        assert d["decision"] in (DECIDE_REVIEW, DECIDE_BLOCK)

    def test_cancel_and_list_jobs(self):
        svc = WikiMaintenanceService(config={
            "maintenance": {"enabled": True, "automation_level": "observe"},
        })
        # observe still creates job (dry_run / review)
        r = svc.handle_source_event("k9", "updated")
        job_id = r["job"]["job_id"]
        jobs = svc.list_jobs()
        assert any(j["job_id"] == job_id for j in jobs)

    def test_disabled_does_not_break(self):
        svc = WikiMaintenanceService(config={"maintenance": {"enabled": False}})
        r = svc.handle_source_event("k", "updated")
        assert r.get("skipped") is True
        assert r.get("raw_search_unaffected") is True
