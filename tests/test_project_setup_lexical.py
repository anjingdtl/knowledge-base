"""project_setup lexical_zh 配置位置测试(spec S4 收尾附带 bug 修复)。

rrf_weight_keyword_zh/en 应在 rag 段顶层(hybrid_search.py:178-180 读取位置),
不应嵌在 lexical_zh 子段。
"""
from src.services.project_setup import ProjectSetupService


def test_lexical_zh_defaults_has_no_rrf_weight():
    """_lexical_zh_defaults 不应含 rrf_weight(它属于 rag 顶层)。"""
    defaults = ProjectSetupService._lexical_zh_defaults()
    assert "rrf_weight_keyword_zh" not in defaults
    assert "rrf_weight_keyword_en" not in defaults
    # 核心字段仍在
    assert defaults["enabled"] is True
    assert defaults["dict_path"] == "data/lexical_zh_dict.txt"
    assert defaults["synonym_path"] == "data/lexical_zh_synonyms.txt"


def test_local_config_rag_has_top_level_rrf_weight():
    """_build_local_config 的 rag 段顶层有 rrf_weight_keyword_zh/en。"""
    svc = ProjectSetupService()
    rag = svc._build_local_config()["rag"]
    assert rag["rrf_weight_keyword_zh"] == 0.7
    assert rag["rrf_weight_keyword_en"] == 0.5
    assert "rrf_weight_keyword_zh" not in rag["lexical_zh"]


def test_provider_config_rag_has_top_level_rrf_weight():
    """_build_provider_config 的 rag 段顶层有 rrf_weight_keyword_zh/en。"""
    from src.core.provider_presets import get_provider_preset

    svc = ProjectSetupService()
    preset = get_provider_preset("siliconflow")
    rag = svc._build_provider_config(preset)["rag"]
    assert rag["rrf_weight_keyword_zh"] == 0.7
    assert rag["rrf_weight_keyword_en"] == 0.5
    assert "rrf_weight_keyword_zh" not in rag["lexical_zh"]
