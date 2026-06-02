"""文件解析器测试"""
import tempfile
import os
import pytest

from src.services.file_parser import parse_file


class TestTextParser:
    def test_parse_txt(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Hello World\n第二行", encoding="utf-8")
        results = parse_file(str(f))
        assert len(results) == 1
        result = results[0]
        assert result.title == "test"
        assert "Hello World" in result.content
        assert result.file_type == "txt"

    def test_parse_markdown(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# 标题\n\n段落内容\n## 子标题\n更多内容", encoding="utf-8")
        results = parse_file(str(f))
        assert len(results) == 1
        result = results[0]
        assert result.file_type == "md"
        assert "标题" in result.content

    def test_parse_code(self, tmp_path):
        f = tmp_path / "app.py"
        f.write_text("def hello():\n    print('hi')\n", encoding="utf-8")
        results = parse_file(str(f))
        assert len(results) == 1
        result = results[0]
        assert result.file_type == "code"
        assert "hello" in result.content
        assert "python" in result.content.lower() or "py" in result.content.lower()

    def test_parse_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            parse_file("/nonexistent/file.txt")

    def test_parse_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value", "list": [1, 2, 3]}', encoding="utf-8")
        results = parse_file(str(f))
        assert len(results) == 1
        result = results[0]
        assert result.file_type == "code"
        assert "key" in result.content
