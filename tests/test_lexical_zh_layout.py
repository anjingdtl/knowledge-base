"""W3 配置注入 + init 模板测试。"""
from src.services.project_setup import ProjectSetupService


def test_init_local_injects_lexical_zh():
    cfg = ProjectSetupService().build_config({"local": True})
    lz = cfg["rag"]["lexical_zh"]
    assert lz["dict_path"] == "data/lexical_zh_dict.txt"
    assert lz["synonym_path"] == "data/lexical_zh_synonyms.txt"
    assert lz["rrf_weight_keyword_zh"] == 0.7
    assert lz["rrf_weight_keyword_en"] == 0.5
    # 浅合并未覆盖其他 rag 键
    assert cfg["rag"]["search_mode"] == "blend"


def test_init_provider_injects_lexical_zh():
    cfg = ProjectSetupService().build_config({"provider": "siliconflow"})
    assert cfg["rag"]["lexical_zh"]["dict_path"] == "data/lexical_zh_dict.txt"


def test_lexical_defaults_not_in_wiki_first_defaults():
    """浅合并坑守卫:lexical_zh 不在 _wiki_first_defaults。"""
    wfd = ProjectSetupService._wiki_first_defaults()
    assert "rag" not in wfd or "lexical_zh" not in (wfd.get("rag") or {})


def test_write_layout_creates_dict_templates(tmp_path):
    """init 生成空字典/同义词模板(if not exists 幂等)。"""
    ProjectSetupService().write_wiki_first_layout(tmp_path)
    assert (tmp_path / "data" / "lexical_zh_dict.txt").exists()
    assert (tmp_path / "data" / "lexical_zh_synonyms.txt").exists()


def test_write_layout_idempotent(tmp_path):
    """二次调用不覆盖用户已填内容。"""
    ProjectSetupService().write_wiki_first_layout(tmp_path)
    dict_file = tmp_path / "data" / "lexical_zh_dict.txt"
    dict_file.write_text("FTTR 1000 nz\n", encoding="utf-8")
    ProjectSetupService().write_wiki_first_layout(tmp_path)
    assert "FTTR" in dict_file.read_text(encoding="utf-8")  # 用户内容保留
