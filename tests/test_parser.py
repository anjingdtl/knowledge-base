"""文件解析器测试"""
import pytest

from src.services.file_parser import _extract_pptx_shapes, _remove_pdf_watermarks, parse_file
from src.services.text_encoding import decode_text_bytes, repair_mojibake


class TestTextParser:
    @pytest.mark.parametrize("encoding", ["gbk", "gb18030"])
    def test_decodes_mainland_chinese_legacy_encodings(self, encoding):
        source = "中国电信广西分公司\n智慧家庭场景化展陈规范"

        decoded = decode_text_bytes(source.encode(encoding))

        assert decoded.text == source
        assert decoded.encoding in {"gb18030", "gbk"}

    def test_parse_text_transcodes_gbk_file(self, tmp_path):
        source = "第一章总则：中国电信"
        f = tmp_path / "legacy.txt"
        f.write_bytes(source.encode("gbk"))

        result = parse_file(str(f))[0]

        assert result.content == source

    def test_parse_text_repairs_utf8_mojibake_saved_to_file(self, tmp_path):
        source = "中国电信智慧家庭场景化展陈规范"
        f = tmp_path / "mojibake.md"
        f.write_text(source.encode("utf-8").decode("latin1"), encoding="utf-8")

        result = parse_file(str(f))[0]

        assert result.content == source

    def test_repairs_only_clear_mojibake(self):
        source = "中国电信智慧家庭"
        garbled = source.encode("utf-8").decode("latin1")

        repaired, changed = repair_mojibake(garbled)

        assert changed is True
        assert repaired == source
        unchanged, changed = repair_mojibake("Normal English content")
        assert changed is False
        assert unchanged == "Normal English content"

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
