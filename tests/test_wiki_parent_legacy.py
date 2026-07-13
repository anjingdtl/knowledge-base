"""W2 装配 + legacy 门控回归(S6):container 注入、init 配置注入、legacy 零变化。"""
from __future__ import annotations

from src.services.project_setup import ProjectSetupService
from src.services.wiki_parent_retrieval import WikiParentRetriever


def test_container_has_wiki_parent_retriever():
    """container.wiki_parent_retriever 是 WikiParentRetriever 实例。"""
    from src.core.container import create_container

    container = create_container()
    assert isinstance(container.wiki_parent_retriever, WikiParentRetriever)


def test_rag_pipeline_deps_include_wiki_parent_retriever():
    """RAGService deps 含 wiki_parent_retriever(确保 stage 能被自动注入)。"""
    import src.core.container as container_mod
    src = open(container_mod.__file__, encoding="utf-8").read()
    assert "'wiki_parent_retriever': self.wiki_parent_retriever" in src


def test_init_local_config_has_wiki_parent_child():
    """authoring init 注入 rag.wiki_parent_child 且 enabled。"""
    config = ProjectSetupService().build_config({"local": True, "mode": "authoring"})
    rag = config["rag"]
    assert "wiki_parent_child" in rag
    assert rag["wiki_parent_child"]["enabled"] is True
    assert rag["wiki_parent_child"]["max_parent_chars"] == 2000


def test_init_provider_config_has_wiki_parent_child():
    """authoring provider init 注入 rag.wiki_parent_child。"""
    config = ProjectSetupService().build_config({
        "provider": "siliconflow", "mode": "authoring",
    })
    rag = config["rag"]
    assert "wiki_parent_child" in rag
    assert rag["wiki_parent_child"]["enabled"] is True


def test_wiki_parent_defaults_not_in_wiki_first_defaults():
    """关键(浅合并坑):wiki_parent_child 不在 _wiki_first_defaults 返回值里。"""
    wfd = ProjectSetupService._wiki_first_defaults()
    # _wiki_first_defaults 只含 knowledge_workflow + wiki 顶层键,不含 rag
    assert "rag" not in wfd or "wiki_parent_child" not in (wfd.get("rag") or {})


def test_legacy_config_has_no_wiki_parent_child():
    """老配置(无 wiki_parent_child 段)时 Config.get 返回默认 disabled。"""
    from src.utils.config import Config

    # 模拟 legacy 项目 config.yaml 无 rag.wiki_parent_child 段
    Config.set("rag", {})
    assert Config.get("rag.wiki_parent_child.enabled", False) is False


def test_config_example_has_wiki_parent_section():
    """config.example.yaml 含 rag.wiki_parent_child 段。"""
    src = open("config.example.yaml", encoding="utf-8").read()
    assert "wiki_parent_child:" in src
    assert "max_parent_chars:" in src
