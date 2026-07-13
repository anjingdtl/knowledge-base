"""WikiEntityUpdater 测试(mock LLM,零真实调用)。"""

import pytest

from src.services.wiki_entity_updater import WikiEntityUpdater
from src.utils.config import Config


class FakeLLM:
    """记录调用次数,按 prompt 里的实体名返回 create JSON。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, silent=False):
        self.calls += 1
        import json
        import re
        m = re.search(r"实体名: (.+)", messages[0]["content"])
        entity = m.group(1).strip() if m else "X"
        return json.dumps({
            "action": "create",
            "summary": f"{entity} 是一个关键概念。",
            "facts": [f"{entity} 在源中被提及"],
            "contradictions": [],
        })


@pytest.fixture
def wiki_dirs(tmp_path):
    Config.set("knowledge_workflow.wiki_dir", str(tmp_path / "wiki"))
    Config.set("knowledge_workflow.entity_dir", str(tmp_path / "wiki" / "entities"))
    Config.set("knowledge_workflow.concept_dir", str(tmp_path / "wiki" / "concepts"))
    Config.set("wiki.max_llm_calls_per_ingest", 3)
    return tmp_path


def test_update_returns_entity_suggestions(wiki_dirs):
    fake = FakeLLM()
    updater = WikiEntityUpdater(llm=fake)
    result = updater.update(
        "kid-1",
        {"key_entities": ["FooService", "BarModule", "Baz"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["llm_calls"] == 3
    assert result["entities_created"] == 0
    assert result["concepts_created"] == 0
    assert len(result["suggestions"]) == 3
    assert {s["entity"] for s in result["suggestions"]} == {"FooService", "BarModule", "Baz"}
    assert not (wiki_dirs / "wiki" / "entities").exists()
    assert not (wiki_dirs / "wiki" / "concepts").exists()


def test_update_returns_suggestions_without_writing_pages(wiki_dirs):
    fake = FakeLLM()
    updater = WikiEntityUpdater(llm=fake)
    result = updater.update(
        "kid-1",
        {"key_entities": ["FooService"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )

    assert result["entities_created"] == 0
    assert result["concepts_created"] == 0
    assert len(result["suggestions"]) == 1
    suggestion = result["suggestions"][0]
    assert suggestion["entity"] == "FooService"
    assert suggestion["source_ids"] == ["kid-1"]
    assert not (wiki_dirs / "wiki" / "entities").exists()
    assert not (wiki_dirs / "wiki" / "concepts").exists()


def test_update_respects_max_calls(wiki_dirs):
    """5 实体但 max=3 → 只 3 次 LLM。"""
    Config.set("wiki.max_llm_calls_per_ingest", 3)
    fake = FakeLLM()
    updater = WikiEntityUpdater(llm=fake)
    result = updater.update(
        "kid-1",
        {"key_entities": ["A1", "B2", "C3", "D4", "E5"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["llm_calls"] == 3
    assert fake.calls == 3


def test_update_no_entities_skips_llm(wiki_dirs):
    fake = FakeLLM()
    updater = WikiEntityUpdater(llm=fake)
    result = updater.update(
        "kid-1",
        {"key_entities": [], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["llm_calls"] == 0
    assert fake.calls == 0
    assert result["entities_created"] == 0


def test_update_marks_contradictions(wiki_dirs):
    class ContradictionLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, silent=False):
            self.calls += 1
            import json
            return json.dumps({
                "action": "update",
                "summary": "更新",
                "facts": [],
                "contradictions": ["新源称 FooService v3,旧页称 v2"],
            })

    updater = WikiEntityUpdater(llm=ContradictionLLM())
    result = updater.update(
        "kid-1",
        {"key_entities": ["FooService"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["contradictions"]  # 非空
    assert result["suggestions"], "entity 建议未生成"
    assert "CONTRADICTION" in result["suggestions"][0]["body"]
    assert not (wiki_dirs / "wiki" / "entities").exists()


def test_update_llm_failure_skipped(wiki_dirs):
    class FailLLM:
        def chat(self, messages, silent=False):
            raise RuntimeError("api down")

    updater = WikiEntityUpdater(llm=FailLLM())
    result = updater.update(
        "kid-1",
        {"key_entities": ["FooService"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["entities_created"] == 0
    assert result["llm_calls"] == 0  # 失败不计成功
