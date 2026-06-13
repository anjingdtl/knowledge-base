"""文件解析器测试"""
import pytest

from src.services.file_parser import _extract_pptx_shapes, _remove_pdf_watermarks, parse_file


class TestTextParser:
    def test_pptx_group_extraction_failure_is_skipped(self):
        class BrokenGroupShape:
            shape_type = 6

            @property
            def shapes(self):
                raise RuntimeError("broken group")

        assert _extract_pptx_shapes([BrokenGroupShape()]) == []

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

    def test_pdf_watermark_keywords_are_removed_inside_content_lines(self):
        pages = [
            "CONFIDENTIAL Project Alpha revenue grew 20%",
            "CONFIDENTIAL Project Beta churn fell 5%",
            "CONFIDENTIAL Project Gamma launched in Q3",
        ]

        cleaned = _remove_pdf_watermarks(pages)

        assert all("CONFIDENTIAL" not in page for page in cleaned)
        assert "Project Alpha revenue grew 20%" in cleaned[0]

    def test_excel_structured_row_keeps_full_row_context(self, tmp_path):
        from openpyxl import Workbook

        f = tmp_path / "sales.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Q1"
        ws.append(["Product", "Revenue", "Owner"])
        ws.append(["Alpha", 100, "Team A"])
        wb.save(f)
        wb.close()

        result = parse_file(str(f))[0]

        assert result.structured
        row = result.structured[0]
        assert "Product=Alpha" in row.content
        assert "Revenue=100" in row.content
        assert "Owner=Team A" in row.content
        assert row.children
