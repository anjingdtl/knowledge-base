"""分词和 FTS 查询清洗测试"""
from src.utils.chinese_tokenizer import (
    sanitize_fts_query,
    tokenize_chinese_full,
)


class TestTokenizeChineseFull:
    def test_chinese(self):
        result = tokenize_chinese_full("企微集约运营情况汇总表")
        tokens = result.split()
        assert "集约" in tokens
        assert "运营" in tokens
        assert "汇总" in tokens
        assert len(tokens) > 3  # 全模式产出多个词组

    def test_mixed(self):
        result = tokenize_chinese_full("2025-2026年企微运营")
        tokens = result.split()
        assert len(tokens) > 0
        assert any("2025" in t for t in tokens)

    def test_empty(self):
        assert tokenize_chinese_full("") == ""
        assert tokenize_chinese_full("   ") == ""


class TestSanitizeFtsQuery:
    def test_plain_query(self):
        assert sanitize_fts_query("企微运营") == '"企微运营"'

    def test_hyphen(self):
        assert sanitize_fts_query("2025-2026") == '"2025-2026"'

    def test_parentheses(self):
        result = sanitize_fts_query("订正版(修正)")
        assert result == '"订正版(修正)"'

    def test_tokenized_input(self):
        result = sanitize_fts_query("企微 集约 运营", is_tokenized=True)
        assert result == '"企微" OR "集约" OR "运营"'

    def test_empty(self):
        assert sanitize_fts_query("") == ""
        assert sanitize_fts_query("   ") == ""

    def test_quotes_in_input(self):
        result = sanitize_fts_query('含"引号"内容')
        assert '"' not in result[1:-1] or result.count('"') == 2
