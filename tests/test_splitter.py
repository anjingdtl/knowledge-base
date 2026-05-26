"""文本分块测试"""
import pytest

from src.services.text_splitter import split_text, split_markdown, split_code


class TestSplitText:
    def test_basic_split(self):
        text = ("段落一" * 100 + "\n\n" + "段落二" * 100 + "\n\n" + "段落三" * 100)
        chunks = split_text(text, chunk_size=300, chunk_overlap=50)
        assert len(chunks) >= 2

    def test_empty_text(self):
        chunks = split_text("")
        assert chunks == []

    def test_short_text(self):
        chunks = split_text("短文本")
        assert len(chunks) == 1
        assert chunks[0].text == "短文本"

    def test_metadata_preserved(self):
        chunks = split_text("测试内容", metadata={"source": "test"})
        assert chunks[0].metadata["source"] == "test"

    def test_chunk_indices(self):
        text = "\n\n".join(["段落" + str(i) * 100 for i in range(10)])
        chunks = split_text(text, chunk_size=200, chunk_overlap=20)
        for i, c in enumerate(chunks):
            assert c.index == i


class TestSplitMarkdown:
    def test_md_headers(self):
        text = "# 标题1\n内容1\n\n## 标题2\n内容2\n\n# 标题3\n内容3"
        chunks = split_markdown(text, chunk_size=500)
        assert len(chunks) >= 1

    def test_empty_md(self):
        assert split_markdown("") == []


class TestSplitCode:
    def test_code_split(self):
        code = "def foo():\n    pass\n\n" * 200
        chunks = split_code(code, chunk_size=800)
        assert len(chunks) >= 1

    def test_empty_code(self):
        assert split_code("") == []
