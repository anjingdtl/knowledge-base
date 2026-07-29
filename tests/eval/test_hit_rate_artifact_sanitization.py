"""Artifact sanitizer must strip secrets and fulltext."""

from __future__ import annotations

import json

from evals.hit_rate_v2.sanitize import sanitize_case_result, sanitize_metrics_report


def test_sanitizer_redacts_phone_email_and_keys():
    payload = {
        "case_id": "KB-032",
        "answer": "联系人手机 13812345678 邮箱 user@example.com",
        "text": "全文证据不应进入 Git",
        "content": "另一段全文",
        "raw_evidence_used": [{"text": "秘密段落", "passage_id": "p1"}],
        "authorization": "Bearer sk-secret-token-value",
        "api_key": "sk-abcdefghijklmnop",
        "meta": {
            "path": "D:\\AiWorkSpace\\knowledge-base\\data\\kb.db",
            "Authorization": "token-xyz",
        },
        "sources": [{"knowledge_id": "k1", "passage_id": "p1"}],
    }
    out = sanitize_case_result(payload)
    blob = json.dumps(out, ensure_ascii=False)
    assert "13812345678" not in blob
    assert "user@example.com" not in blob
    assert "sk-abcdefghijklmnop" not in blob
    assert "Bearer sk-secret" not in blob
    assert "全文证据不应进入 Git" not in blob
    assert "秘密段落" not in blob
    assert out["case_id"] == "KB-032"
    assert out["sources"][0]["passage_id"] == "p1"
    # Fulltext fields replaced by redaction envelope
    assert out["answer"]["_redacted"] is True
    assert out["text"]["_redacted"] is True


def test_metrics_report_keeps_ids_and_reason_codes():
    report = {
        "metrics": {"Top-1 Accuracy": 0.8, "False Positive Rate": 1.0},
        "detail": [
            {
                "case_id": "KB-032",
                "false_positive": True,
                "reason_codes": ["unexpected_answer_mode"],
                "answer": "should be stripped from detail projection",
                "defect_category": "false_positive",
            }
        ],
    }
    out = sanitize_metrics_report(report)
    assert out["metrics"]["Top-1 Accuracy"] == 0.8
    assert out["detail"][0]["case_id"] == "KB-032"
    assert out["detail"][0]["reason_codes"] == ["unexpected_answer_mode"]
    assert "answer" not in out["detail"][0]
