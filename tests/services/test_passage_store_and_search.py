"""Integration tests for passage store + hybrid search unit (SPEC v3)."""
from __future__ import annotations

import pytest

from src.services.passage_builder import build_passages_for_document
from src.services.passage_store import PassageStore
from src.services.version_rank import family_key_of, filter_to_latest_versions


@pytest.fixture()
def passage_store(tmp_path, monkeypatch):
    """Isolated sqlite db via Database test fixture if available."""
    # Prefer project conftest setup_db which resets Database singleton.
    from src.services.db import Database

    store = PassageStore(db=Database)
    store.ensure_schema()
    # Clear any residual passages from parallel tests.
    try:
        conn = Database.get_conn()
        conn.execute("DELETE FROM retrieval_passages")
        conn.execute("DELETE FROM passage_fts")
        try:
            conn.execute("DELETE FROM vec_passages")
        except Exception:
            pass
        conn.commit()
    except Exception:
        pass
    return store


class TestPassageStore:
    def test_upsert_and_fts_roundtrip(self, passage_store):
        drafts = build_passages_for_document(
            knowledge_id="k-fts",
            title="涉诈电话处置细则",
            content=(
                "第六条 对涉及的代理商营业员、代理商网点，分涉诈、涉骚扰电话号码，"
                "每个号码一个自然月内处罚2000元。" * 5
            ),
            blocks=[
                {
                    "id": "blk1",
                    "content": "第六条 对涉及的代理商营业员、代理商网点，分涉诈、涉骚扰电话号码，每个号码一个自然月内处罚2000元。",
                    "order_idx": 0,
                }
            ],
        )
        rows = [d.to_row() for d in drafts]
        n = passage_store.upsert_passages(rows)
        assert n >= 1
        hits = passage_store.fts_search("涉诈 代理商 处罚 2000", top_k=5)
        assert hits, "FTS should hit the passage"
        assert hits[0].get("knowledge_id") == "k-fts"
        assert hits[0].get("passage_id") or hits[0].get("id")

    def test_delete_by_knowledge_cleans_fts(self, passage_store):
        drafts = build_passages_for_document(
            knowledge_id="k-del",
            title="删除测试",
            content="这是用于删除测试的足够长的正文内容。" * 20,
        )
        passage_store.upsert_passages([d.to_row() for d in drafts])
        assert passage_store.get_by_knowledge("k-del")
        passage_store.delete_by_knowledge("k-del")
        assert passage_store.get_by_knowledge("k-del") == []

    def test_health_stats_shape(self, passage_store):
        drafts = build_passages_for_document(
            knowledge_id="k-health",
            title="健康度",
            content=("健康度统计用正文内容，包含足够字符。" * 30),
        )
        passage_store.upsert_passages([d.to_row() for d in drafts])
        h = passage_store.health_stats()
        assert h["retrieval_index_unit"] == "passage"
        assert h["passages"] >= 1
        assert "vector_coverage" in h
        assert "fts_coverage" in h
        assert "avg_char_count" in h


class TestVersionFamilyFilter:
    def test_latest_version_excludes_old_edition(self):
        items = [
            {
                "knowledge_id": "old",
                "title": "技能竞赛管理办法-2023",
                "text": "一级竞赛 二级竞赛",
                "document_family_id": "topic:技能竞赛管理办法",
                "version_year": 2023,
                "score": 0.9,
            },
            {
                "knowledge_id": "new",
                "title": "技能竞赛管理办法-2026",
                "text": "取消一级二级分级",
                "document_family_id": "topic:技能竞赛管理办法",
                "version_year": 2026,
                "score": 0.85,
            },
            {
                "knowledge_id": "other",
                "title": "差旅费管理办法-2025",
                "text": "住宿费",
                "document_family_id": "topic:差旅费管理办法",
                "version_year": 2025,
                "score": 0.5,
            },
        ]
        kept = filter_to_latest_versions(items)
        kids = {k.get("knowledge_id") for k in kept}
        assert "new" in kids
        assert "old" not in kids
        assert "other" in kids
        assert family_key_of(items[0]) == family_key_of(items[1])


class TestNumericGuardMultiCondition:
    def test_kb010_style_no_false_refuse(self):
        from src.answering.fact_guard import (
            answer_numerics_supported_by_evidence,
            extract_query_conditions,
            strip_unanchored_numeric_assertions,
        )

        q = "防诈骗和骚扰电话 代理商被罚多少钱"
        evidence = (
            "第六条 对涉及的代理商营业员、代理商网点，分涉诈、涉骚扰电话号码，"
            "每个号码一个自然月内处罚2000元。"
        )
        answer = "代理商涉诈/涉骚扰号码每个自然月处罚2000元。"
        conds = extract_query_conditions(q)
        assert "涉诈" in conds or "涉骚扰" in conds
        cleaned, stripped = strip_unanchored_numeric_assertions(
            answer, evidence=evidence, question=q,
        )
        assert "2000" in cleaned
        assert answer_numerics_supported_by_evidence(
            answer=cleaned, evidence=evidence, condition=None,
        )
