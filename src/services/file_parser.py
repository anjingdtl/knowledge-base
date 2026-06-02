"""多格式文件解析器"""
import os
from pathlib import Path
from dataclasses import dataclass


def _read_file_text(path: Path) -> str:
    """自动检测编码并读取文本文件"""
    raw = path.read_bytes()
    if not raw:
        return ""
    from charset_normalizer import from_bytes
    result = from_bytes(raw)
    if result and result.best():
        return str(result.best())
    return raw.decode("utf-8", errors="replace")


@dataclass
class ParsedFile:
    title: str
    content: str
    file_type: str
    source_path: str
    metadata: dict


def parse_file(file_path: str) -> list[ParsedFile]:
    """解析文件，返回 ParsedFile 列表（大多数格式返回单元素列表，Excel 返回多元素）"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = path.suffix.lower()
    parsers = {
        ".pdf": _parse_pdf,
        ".pptx": _parse_pptx,
        ".ppt": _parse_pptx,
        ".docx": _parse_docx,
        ".doc": _parse_docx,
        ".xlsx": _parse_excel,
        ".xls": _parse_excel,
        ".csv": _parse_csv,
        ".txt": _parse_text,
        ".md": _parse_markdown,
        ".html": _parse_html,
        ".htm": _parse_html,
    }
    code_extensions = {
        ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs",
        ".rb", ".php", ".cs", ".swift", ".kt", ".scala", ".sh", ".bat",
        ".sql", ".r", ".m", ".json", ".yaml", ".yml", ".xml", ".toml",
    }

    if ext in parsers:
        result = parsers[ext](path)
        # Excel 解析器直接返回 list[ParsedFile]
        if isinstance(result, list):
            return result
        return [result]
    elif ext in code_extensions:
        return [_parse_code(path)]
    elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
        return [_parse_image(path)]
    else:
        return [_parse_text(path)]


MAX_ROWS_PER_SHEET = 500


def _looks_like_data(value) -> bool:
    s = str(value).strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        pass
    import re
    if re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}$', s):
        return True
    return False


def _detect_headers(first_row: list) -> list[str]:
    non_empty = [v for v in first_row if str(v).strip()]
    if not non_empty:
        return []
    all_stringish = all(
        not _looks_like_data(v)
        for v in non_empty
    )
    if all_stringish:
        return [str(v).strip() for v in first_row]
    return []


def _table_rows_to_text(headers: list[str], rows: list[list[str]], sheet_name: str = "") -> str:
    lines = []
    if sheet_name:
        lines.append(f"[工作表: {sheet_name}]")
    if headers:
        lines.append(f"表头: {' | '.join(headers)}")
    lines.append("")

    total = len(rows)
    truncated = total > MAX_ROWS_PER_SHEET
    row_limit = min(total, MAX_ROWS_PER_SHEET)

    for i in range(row_limit):
        row = rows[i]
        parts = []
        for col_idx, val in enumerate(row):
            val = str(val).strip() if val is not None else ""
            if not val:
                continue
            header = headers[col_idx] if col_idx < len(headers) else f"列{col_idx + 1}"
            parts.append(f"{header}={val}")
        if not parts:
            continue
        lines.append(f"第{i + 1}行: {', '.join(parts)}")

    if truncated:
        lines.append(f"\n（共{total}行，已截取前{MAX_ROWS_PER_SHEET}行）")

    return "\n".join(lines)


def _extract_pptx_shapes(shapes) -> list[str]:
    """递归提取形状中的文本（含组合形状）"""
    parts = []
    for shape in shapes:
        # 组合形状：递归提取子形状
        if shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
            try:
                parts.extend(_extract_pptx_shapes(shape.shapes))
            except Exception:
                pass
            continue
        # 普通文本框/标题
        if shape.has_text_frame:
            text = shape.text_frame.text.strip()
            if text:
                parts.append(text)
        # 表格
        if shape.has_table:
            rows = []
            for row in shape.table.rows:
                cells = [cell.text_frame.text.strip() for cell in row.cells]
                rows.append(" | ".join(cells))
            table_text = "\n".join(rows)
            if table_text.strip():
                parts.append(table_text)
    return parts


def _parse_pptx(path: Path) -> ParsedFile:
    from pptx import Presentation
    from pptx.util import Emu
    import re as _re

    prs = Presentation(str(path))
    slide_count = len(prs.slides)
    slides = []

    for i, slide in enumerate(prs.slides):
        parts = _extract_pptx_shapes(slide.shapes)

        # 备注页
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"[备注] {notes}")

        if parts:
            cleaned = []
            for p in parts:
                # 清理控制字符和特殊项目符号
                p = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", p)
                p = p.replace("\r\n", "\n").strip()
                if p:
                    cleaned.append(p)
            if cleaned:
                slides.append(f"[第{i+1}页]\n" + "\n\n".join(cleaned))

    content = "\n\n".join(slides)

    # 从首页提取标题（首个非空短文本）
    title = path.stem
    if prs.slides:
        for shape in prs.slides[0].shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t and len(t) <= 80:
                    title = t
                    break

    return ParsedFile(
        title=title,
        content=content,
        file_type="pptx",
        source_path=str(path),
        metadata={"slides": slide_count},
    )


def _parse_pdf(path: Path) -> ParsedFile:
    from io import BytesIO
    from PyPDF2 import PdfReader

    reader = PdfReader(str(path))

    if reader.is_encrypted:
        # 先尝试 PyPDF2 空密码解密（大多数 owner-only 加密场景）
        decrypt_result = reader.decrypt("")
        if decrypt_result == 0:
            # PyPDF2 解密失败，尝试 pikepdf 解密
            try:
                import pikepdf
                with pikepdf.open(str(path)) as pdf:
                    buf = BytesIO()
                    pdf.save(buf)
                    buf.seek(0)
                reader = PdfReader(buf)
            except ImportError:
                raise ValueError(
                    f"PDF 文件已加密 ({path.name})，"
                    f"需要安装 pikepdf 才能导入: pip install pikepdf"
                )
            except Exception:
                raise ValueError(
                    f"PDF 文件已加密且需要密码才能打开 ({path.name})，"
                    f"请先解密或提供密码后重试"
                )

    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            pages.append(f"[第{i+1}页]\n{text}")
    content = "\n\n".join(pages)

    metadata = {"pages": len(reader.pages)}
    if reader.is_encrypted:
        metadata["encrypted"] = True

    return ParsedFile(
        title=path.stem,
        content=content,
        file_type="pdf",
        source_path=str(path),
        metadata=metadata,
    )


def _parse_docx(path: Path) -> ParsedFile:
    from docx import Document
    doc = Document(str(path))
    paragraphs = []
    for para in doc.paragraphs:
        if para.text.strip():
            paragraphs.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            paragraphs.append(" | ".join(cells))
    content = "\n\n".join(paragraphs)
    return ParsedFile(
        title=path.stem,
        content=content,
        file_type="docx",
        source_path=str(path),
        metadata={},
    )


def _parse_text(path: Path) -> ParsedFile:
    content = _read_file_text(path)
    return ParsedFile(
        title=path.stem,
        content=content,
        file_type="txt",
        source_path=str(path),
        metadata={},
    )


def _parse_markdown(path: Path) -> ParsedFile:
    content = _read_file_text(path)
    return ParsedFile(
        title=path.stem,
        content=content,
        file_type="md",
        source_path=str(path),
        metadata={},
    )


def _parse_html(path: Path) -> ParsedFile:
    from bs4 import BeautifulSoup
    content = _read_file_text(path)
    soup = BeautifulSoup(content, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else path.stem
    text = _extract_main_content(soup)
    return ParsedFile(
        title=title,
        content=text,
        file_type="html",
        source_path=str(path),
        metadata={},
    )


def _extract_main_content(soup) -> str:
    """从 HTML 中提取正文区域，优先定位 <article>/<main>，回退全文"""
    REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside",
                   "form", "iframe", "noscript", "svg", "button", "input",
                   "select", "textarea"}
    REMOVE_ROLES = {"navigation", "banner", "complementary", "contentinfo",
                    "search", "toolbar", "menu", "menubar"}

    # 先移除无关标签
    for tag in soup.find_all(lambda t: t.name in REMOVE_TAGS):
        tag.decompose()
    for tag in soup.find_all(lambda t: t.get("role", "") in REMOVE_ROLES):
        tag.decompose()
    # 移除广告、评论区等常见非正文容器
    for cls_pattern in ["ad", "ads", "advert", "comment", "sidebar",
                        "social", "share", "related", "recommend", "widget",
                        "cookie", "popup", "modal", "banner", "toolbar"]:
        for tag in soup.find_all(class_=lambda c: c and cls_pattern in " ".join(c).lower()):
            tag.decompose()

    # 优先定位正文容器
    main = None
    for selector in [
        lambda s: s.find("article"),
        lambda s: s.find("main"),
        lambda s: s.find(attrs={"role": "main"}),
        lambda s: s.find(class_=lambda c: c and any(
            k in " ".join(c).lower()
            for k in ["article", "post", "content", "entry", "body"]
        )),
        lambda s: s.find(id=lambda i: i and any(
            k in i.lower()
            for k in ["article", "post", "content", "entry", "body"]
        )),
    ]:
        candidate = selector(soup)
        if candidate:
            text = candidate.get_text(separator="\n", strip=True)
            if len(text) > 200:
                main = candidate
                break

    if main:
        text = main.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # 清理：合并连续空行
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_url(url: str, timeout: float | None = None) -> ParsedFile:
    """抓取网页并提取正文文本"""
    import httpx
    import re
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse

    if not url.startswith(("http://", "https://")):
        raise ValueError(f"不支持的 URL 协议: {url}")

    if timeout is None:
        from src.utils.config import Config
        timeout = Config.get("web.timeout", 30)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {url}")

    # 优先用原始 bytes + BS4 自动编码检测，避免 httpx 误判编码
    raw_bytes = response.content
    soup = BeautifulSoup(raw_bytes, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        # 清理标题中的网站后缀
        title = re.split(r"\s*[-_|–—]\s*", title)[0].strip()
    if not title:
        path = urlparse(url).path
        title = path.rstrip("/").rsplit("/", 1)[-1] or urlparse(url).netloc

    text = _extract_main_content(soup)

    if not text or len(text) < 50:
        raise RuntimeError(
            f"网页正文为空或过短（{len(text)} 字符），可能是 SPA 动态页面无法抓取: {url}"
        )

    return ParsedFile(
        title=title,
        content=text,
        file_type="html",
        source_path=url,
        metadata={"url": url, "status_code": response.status_code},
    )


def _parse_code(path: Path) -> ParsedFile:
    content = _read_file_text(path)
    lang = path.suffix.lstrip(".")
    header = f"文件: {path.name} (语言: {lang})\n{'=' * 40}\n"
    return ParsedFile(
        title=path.name,
        content=header + content,
        file_type="code",
        source_path=str(path),
        metadata={"language": lang},
    )


def _parse_image(path: Path) -> ParsedFile:
    from PIL import Image
    img = Image.open(str(path))
    metadata = {
        "format": img.format,
        "size": f"{img.width}x{img.height}",
        "mode": img.mode,
    }
    content = f"[图片文件: {path.name}]\n尺寸: {img.width}x{img.height}\n格式: {img.format or '未知'}"
    return ParsedFile(
        title=path.stem,
        content=content,
        file_type="image",
        source_path=str(path),
        metadata=metadata,
    )


def _parse_excel(path: Path) -> list[ParsedFile]:
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        wb = load_workbook(str(path), data_only=True)
    except InvalidFileException:
        raise ValueError(
            f"不支持旧版 .xls 格式 ({path.name})，请先转换为 .xlsx 格式"
        )

    results = []
    sheet_names = list(wb.sheetnames)

    for name in sheet_names:
        ws = wb[name]

        merged_values = {}
        for merged_range in ws.merged_cells.ranges:
            top_left_val = ws.cell(row=merged_range.min_row, column=merged_range.min_col).value
            for r in range(merged_range.min_row, merged_range.max_row + 1):
                for c in range(merged_range.min_col, merged_range.max_col + 1):
                    if (r, c) != (merged_range.min_row, merged_range.min_col):
                        merged_values[(r, c)] = top_left_val

        raw_rows = []
        for row in ws.iter_rows(values_only=False):
            cells = []
            for cell in row:
                val = merged_values.get((cell.row, cell.column), cell.value)
                cells.append(str(val) if val is not None else "")
            raw_rows.append(cells)

        if not raw_rows:
            continue

        headers = _detect_headers(raw_rows[0])
        data_rows = raw_rows[1:] if headers else raw_rows

        text = _table_rows_to_text(headers, data_rows, sheet_name=name)
        if not text.strip():
            continue

        # 每个 sheet 独立知识条目，标题格式：文件名 - Sheet名
        title = f"{path.stem} - {name}" if len(sheet_names) > 1 else path.stem

        results.append(ParsedFile(
            title=title,
            content=text,
            file_type="xlsx",
            source_path=str(path),
            metadata={"sheet_name": name, "rows": len(data_rows), "sheets_total": len(sheet_names)},
        ))

    wb.close()

    # 无有效 sheet 时返回空列表（调用方应处理）
    if not results:
        results.append(ParsedFile(
            title=path.stem,
            content="",
            file_type="xlsx",
            source_path=str(path),
            metadata={"sheets": len(sheet_names)},
        ))

    return results


def _parse_csv(path: Path) -> ParsedFile:
    import csv
    import io

    text = _read_file_text(path)
    if not text.strip():
        return ParsedFile(
            title=path.stem, content="", file_type="csv",
            source_path=str(path), metadata={},
        )

    reader = csv.reader(io.StringIO(text))
    raw_rows = [[cell.strip() for cell in row] for row in reader]

    if not raw_rows:
        return ParsedFile(
            title=path.stem, content="", file_type="csv",
            source_path=str(path), metadata={},
        )

    headers = _detect_headers(raw_rows[0])
    data_rows = raw_rows[1:] if headers else raw_rows

    content = _table_rows_to_text(headers, data_rows, sheet_name="")

    return ParsedFile(
        title=path.stem,
        content=content,
        file_type="csv",
        source_path=str(path),
        metadata={"rows": len(data_rows)},
    )
