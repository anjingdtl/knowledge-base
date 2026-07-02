"""Task 1.4 装配与门控测试(spec S6):container 注入、init 配置注入、
legacy/wiki_first 模式门控。

agentic_router 不挂 SizeAwareRouter(scale 完全由管线 WikiReadStage 管,见
Task 1.2 决策),故 legacy 门控在 stage 层 + config 层双重保证。
"""
from __future__ import annotations

import pytest

from src.services.project_setup import ProjectSetupService
from src.utils.config import Config


def test_init_local_injects_size_aware_config():
    config = ProjectSetupService().build_config({"local": True})
    size_aware = config.get("rag", {}).get("size_aware")
    assert size_aware is not None, "init 应注入 rag.size_aware 段"
    assert size_aware["enabled"] is True
    assert size_aware["small_query_max_tokens"] == 12
    assert size_aware["small_wiki_page_threshold"] == 3
    assert "哪些" in size_aware["intent_words_large"]
    assert size_aware["llm_fallback"] is False
    # 浅 update 未覆盖:knowledge_workflow.mode 仍在
    assert config["knowledge_workflow"]["mode"] == "wiki_first"
    # 浅 update 未覆盖:local rag 的其他键仍在
    assert config["rag"]["search_mode"] == "blend"


def test_init_provider_injects_size_aware_config():
    config = ProjectSetupService().build_config({"provider": "siliconflow"})
    assert config["rag"]["size_aware"]["enabled"] is True
    assert config["knowledge_workflow"]["mode"] == "wiki_first"


def test_container_injects_size_aware_router():
    from src.core.container import create_container
    from src.services.size_aware_router import SizeAwareRouter
    from src.services.wiki_page_locator import WikiPageLocator

    container = create_container()
    locator = container.wiki_page_locator
    router = container.size_aware_router
    assert isinstance(locator, WikiPageLocator)
    assert isinstance(router, SizeAwareRouter)
    # router 持有 container 注入的同一 locator 实例(依赖注入闭环)
    assert router._locator is locator


def test_legacy_config_size_aware_disabled():
    # 模拟 legacy 项目 config.yaml 无 rag.size_aware 段 → Config.get 缺省 false
    Config.set("rag", {})
    assert Config.get("rag.size_aware.enabled", False) is False


@pytest.mark.asyncio
async def test_size_aware_enabled_in_wiki_first_sets_scale():
    # wiki_first + enabled → WikiReadStage 设 ctx.metadata["scale"](管线装配闭环)
    Config.set("knowledge_workflow.mode", "wiki_first")
    Config.set("rag.size_aware.enabled", True)
    from src.services.rag_pipeline import RagContext, WikiReadStage

    class _FakeRouter:
        def route(self, q):
            return {"scale": "wiki_read", "reason": "ok", "wiki_hits": 2}

    class _FakeLocator:
        def locate(self, q, top_n=10):
            return [{"id": "wiki:s:x", "text": "t", "metadata": {},
                     "match_channels": ["wiki_read"]}], 1

    stage = WikiReadStage(size_aware_router=_FakeRouter(), wiki_page_locator=_FakeLocator())
    ctx = RagContext(question="FTTR")
    ctx = await stage.execute(ctx, {"enabled": True})
    assert ctx.metadata.get("scale") == "wiki_read"
