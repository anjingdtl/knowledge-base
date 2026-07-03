"""detect_query_language 语种判定单测（纯算法，无 jieba/IO）。"""
from src.utils.chinese_tokenizer import detect_query_language


def test_chinese_query_is_zh():
    assert detect_query_language("知识库默认使用什么数据库") == "zh"


def test_english_query_is_en():
    assert detect_query_language("what is RAG") == "en"


def test_mixed_chinese_english_is_zh():
    assert detect_query_language("FTTR 是什么") == "zh"  # 含汉字即 zh


def test_empty_defaults_en():
    assert detect_query_language("") == "en"


def test_pure_english_acronym_is_en():
    assert detect_query_language("RAG") == "en"
