"""结构化内容块数据模型 — 用于文件解析阶段的层级化输出

各格式解析器（Excel/PDF/DOCX/PPT）产出 StructuredBlock 树，
由 file_graph 转换为 OutlineBlock 并保留 parent-child 层级关系。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StructuredBlock:
    """结构化内容块 — 带层级和属性

    Attributes:
        content: Block 文本内容
        block_type: 内容类型 — text | heading | table_row | slide | code_block | property
        children: 子 Block 列表（形成树结构）
        properties: 格式特定的元数据（如 sheet 名、列名、页码等）
        level: 层级深度（0=顶层），用于 heading 类型的级别
    """

    content: str = ""
    block_type: str = "text"
    children: list["StructuredBlock"] = field(default_factory=list)
    properties: dict = field(default_factory=dict)
    level: int = 0

    def _walk(self, result, parent_content, depth):
        result.append((self, parent_content, depth))
        for child in self.children:
            child._walk(result, self.content, depth + 1)

    def _serialize(self, lines: list[str], depth: int):
        indent = "  " * depth
        lines.append(f"{indent}- {self.content}")
        for child in self.children:
            child._serialize(lines, depth + 1)


def flatten_block_tree(blocks: list[StructuredBlock]) -> list[tuple[StructuredBlock, str | None, int]]:
    """展平 StructuredBlock 树

    Returns:
        [(block, parent_content_or_None, depth), ...]
    """
    result: list[tuple[StructuredBlock, str | None, int]] = []

    def walk(items: list[StructuredBlock], parent_content: str | None, depth: int):
        for block in items:
            result.append((block, parent_content, depth))
            walk(block.children, block.content, depth + 1)

    walk(blocks, None, 0)
    return result


def blocks_to_plain_text(blocks: list[StructuredBlock]) -> str:
    """将 StructuredBlock 列表序列化为 Markdown 大纲格式（Logseq 风格）"""
    lines = []

    def walk(items: list[StructuredBlock], depth: int):
        for block in items:
            indent = "  " * depth
            lines.append(f"{indent}- {block.content}")
            walk(block.children, depth + 1)

    walk(blocks, 0)
    return "\n".join(lines)
