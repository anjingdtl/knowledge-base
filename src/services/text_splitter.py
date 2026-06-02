"""文本分块策略 — 支持父级标题路径注入"""
import re
from dataclasses import dataclass


@dataclass
class TextChunk:
    text: str
    index: int
    metadata: dict


def split_text(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    metadata: dict | None = None,
) -> list[TextChunk]:
    if not text.strip():
        return []
    base_meta = metadata or {}

    paragraphs = _split_to_paragraphs(text)
    chunks = _merge_paragraphs(paragraphs, chunk_size, chunk_overlap)
    return [TextChunk(text=c, index=i, metadata={**base_meta, "chunk_index": i}) for i, c in enumerate(chunks)]


def _force_split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """对超长文本强制按 chunk_size 切分（按句子或固定长度）"""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    # 优先按句子切分
    sentences = re.split(r'(?<=[。！？；\n])', text)
    current = ""
    for s in sentences:
        if len(current) + len(s) > chunk_size and current:
            chunks.append(current)
            # overlap: 保留末尾部分
            current = current[-overlap:] if overlap > 0 else ""
        current += s
        # 如果单句超长，强制按 chunk_size 切
        while len(current) > chunk_size * 1.5:
            chunks.append(current[:chunk_size])
            current = current[chunk_size - overlap:] if overlap > 0 else current[chunk_size:]
    if current.strip():
        chunks.append(current)
    return chunks


def split_markdown(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    metadata: dict | None = None,
) -> list[TextChunk]:
    if not text.strip():
        return []
    base_meta = metadata or {}

    sections = _parse_md_hierarchy(text)
    paragraphs = []
    for heading_path, section_text in sections:
        prefix = f"[{heading_path}]\n" if heading_path else ""
        paragraphs.append(f"{prefix}{section_text}")

    chunks = _merge_paragraphs(paragraphs, chunk_size, chunk_overlap)
    return [TextChunk(text=c, index=i, metadata={**base_meta, "chunk_index": i}) for i, c in enumerate(chunks)]


def split_code(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 50,
    metadata: dict | None = None,
) -> list[TextChunk]:
    if not text.strip():
        return []
    base_meta = metadata or {}
    chunks = []
    lines = text.split("\n")
    current = []
    for line in lines:
        current.append(line)
        if len("\n".join(current)) >= chunk_size:
            chunks.append("\n".join(current))
            current = current[-max(1, chunk_overlap // max(1, len(line) + 1)):]
    if current:
        chunks.append("\n".join(current))
    return [TextChunk(text=c, index=i, metadata={**base_meta, "chunk_index": i}) for i, c in enumerate(chunks)]


def _split_to_paragraphs(text: str) -> list[str]:
    parts = re.split(r'\n\s*\n', text)
    return [p.strip() for p in parts if p.strip()]


def _parse_md_hierarchy(text: str) -> list[tuple[str, str]]:
    """解析 Markdown 文本，返回 [(heading_path, section_text), ...]

    heading_path 为完整的标题层级路径，如 "# 第一章 > ## 1.2 节 > ### 1.2.1"
    """
    heading_stack = []  # [(level, title), ...]
    results = []
    current_lines = []

    for line in text.split("\n"):
        match = re.match(r'^(#{1,6})\s+(.+)', line)
        if match:
            if current_lines:
                path = " > ".join(f"{'#' * lv} {title}" for lv, title in heading_stack)
                results.append((path, "\n".join(current_lines)))
                current_lines = []
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = [(lv, t) for lv, t in heading_stack if lv < level]
            heading_stack.append((level, title))
        else:
            current_lines.append(line)

    if current_lines:
        path = " > ".join(f"{'#' * lv} {title}" for lv, title in heading_stack)
        results.append((path, "\n".join(current_lines)))

    return [(path, text.strip()) for path, text in results if text.strip()]


def _merge_paragraphs(paragraphs: list[str], chunk_size: int, overlap: int) -> list[str]:
    if not paragraphs:
        return []
    chunks = []
    current_parts = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        # 单段落超长时强制切分
        if para_len > chunk_size * 1.5:
            # 先 flush 已有内容
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_len = 0
            # 强制切分长段落
            sub_chunks = _force_split_long_text(para, chunk_size, overlap)
            chunks.extend(sub_chunks)
            continue
        if current_len + para_len + 1 > chunk_size and current_parts:
            chunks.append("\n\n".join(current_parts))
            tail = "\n\n".join(current_parts)
            tail = tail[-overlap:] if overlap > 0 else ""
            current_parts = [tail] if tail else []
            current_len = len(tail)
        current_parts.append(para)
        current_len += para_len + 2

    if current_parts:
        chunks.append("\n\n".join(current_parts))
    return chunks
