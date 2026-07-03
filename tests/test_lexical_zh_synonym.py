"""LexicalZh 同义词扩展单测（独立模块，不依赖 db/向量）。"""
from src.services.lexical_zh import LexicalZh, expand_query_with_synonyms


def _config_with_synonym(path: str) -> dict:
    return {"rag": {"lexical_zh": {"enabled": True, "synonym_path": path}}}


def test_expand_appends_synonyms(tmp_path):
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库 KB\n# 注释\n\n短词\n", encoding="utf-8")
    lex = LexicalZh(config=_config_with_synonym(str(syn)))
    assert lex.expand_query("知识库") == "知识库 知识仓库 KB"

def test_expand_no_match_returns_original(tmp_path):
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库\n", encoding="utf-8")
    lex = LexicalZh(config=_config_with_synonym(str(syn)))
    assert lex.expand_query("无关词") == "无关词"  # 零回归红线

def test_expand_empty_query(tmp_path):
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库\n", encoding="utf-8")
    lex = LexicalZh(config=_config_with_synonym(str(syn)))
    assert lex.expand_query("") == ""

def test_missing_synonym_file_returns_original(tmp_path):
    """文件缺失 → 容错，expand_query 返回原 query。"""
    lex = LexicalZh(config=_config_with_synonym(str(tmp_path / "nope.txt")))
    assert lex.expand_query("知识库") == "知识库"

def test_disabled_returns_original(tmp_path):
    """enabled=false → 完全 no-op。"""
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库\n", encoding="utf-8")
    cfg = {"rag": {"lexical_zh": {"enabled": False, "synonym_path": str(syn)}}}
    assert LexicalZh(config=cfg).expand_query("知识库") == "知识库"

def test_convenience_function(tmp_path):
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库\n", encoding="utf-8")
    assert "知识仓库" in expand_query_with_synonyms("知识库", config=_config_with_synonym(str(syn)))
