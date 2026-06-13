"""Markdown outliner parser for the file-first graph."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field


@dataclass
class OutlineBlock:
    id: str = ""
    content: str = ""
    children: list["OutlineBlock"] = field(default_factory=list)
    properties: dict = field(default_factory=dict)


@dataclass
class PageDocument:
    id: str = ""
    title: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    blocks: list[OutlineBlock] = field(default_factory=list)


class MarkdownOutlineParser:
    """Parse and serialize the small Logseq-style Markdown subset we own."""

    _PROP_RE = re.compile(r"^([A-Za-z0-9_-]+)::\s*(.*)$")
    _BLOCK_RE = re.compile(r"^(\s*)-\s?(.*)$")
    _INLINE_ID_RE = re.compile(r"\s+id::\s*(\S+)\s*$")

    def parse(self, text: str) -> PageDocument:
        page = PageDocument()
        stack: list[tuple[int, OutlineBlock]] = []
        in_blocks = False
        last_block_at_depth: dict[int, OutlineBlock] = {}

        for raw_line in text.splitlines():
            line = raw_line.rstrip("\n")
            if not in_blocks:
                if not line.strip():
                    continue
                block_match = self._BLOCK_RE.match(line)
                if block_match:
                    in_blocks = True
                else:
                    prop_match = self._PROP_RE.match(line.strip())
                    if prop_match:
                        self._apply_page_property(page, prop_match.group(1), prop_match.group(2))
                    continue

            block_match = self._BLOCK_RE.match(line)
            if not block_match:
                prop_match = self._PROP_RE.match(line.strip())
                if prop_match and line.startswith("  "):
                    depth = max((len(line) - len(line.lstrip(" "))) // 2 - 1, 0)
                    target = last_block_at_depth.get(depth)
                    if target:
                        key = prop_match.group(1)
                        value = prop_match.group(2)
                        if key == "id":
                            target.id = value
                        else:
                            target.properties[key] = value
                    continue
                if stack and line.strip():
                    stack[-1][1].content += "\n" + line.strip()
                continue

            spaces, content = block_match.groups()
            depth = len(spaces) // 2
            block_id = ""
            inline = self._INLINE_ID_RE.search(content)
            if inline:
                block_id = inline.group(1)
                content = self._INLINE_ID_RE.sub("", content).rstrip()
            block = OutlineBlock(id=block_id, content=content)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if stack:
                stack[-1][1].children.append(block)
            else:
                page.blocks.append(block)
            stack.append((depth, block))
            last_block_at_depth[depth] = block

        return page

    def serialize(self, page: PageDocument) -> str:
        self.ensure_ids(page)
        lines = [
            f"id:: {page.id}",
            f"title:: {page.title}",
            f"tags:: {', '.join(page.tags)}",
        ]
        for key, value in page.metadata.items():
            if key in {"id", "title", "tags"} or value in (None, ""):
                continue
            lines.append(f"{key}:: {value}")
        lines.append("")
        for block in page.blocks:
            self._serialize_block(block, 0, lines)
        return "\n".join(lines).rstrip() + "\n"

    def ensure_ids(self, page: PageDocument) -> bool:
        changed = False
        if not page.id:
            page.id = str(uuid.uuid4())
            changed = True
        for block in self.iter_blocks(page.blocks):
            if not block.id:
                block.id = str(uuid.uuid4())
                changed = True
        return changed

    def iter_blocks(self, blocks: list[OutlineBlock]):
        for block in blocks:
            yield block
            yield from self.iter_blocks(block.children)

    def flatten_blocks(self, blocks: list[OutlineBlock]) -> list[tuple[OutlineBlock, str | None, int, int]]:
        flat: list[tuple[OutlineBlock, str | None, int, int]] = []

        def walk(items: list[OutlineBlock], parent_id: str | None, depth: int):
            for idx, block in enumerate(items):
                flat.append((block, parent_id, depth, idx))
                walk(block.children, block.id, depth + 1)

        walk(blocks, None, 0)
        return flat

    def _serialize_block(self, block: OutlineBlock, depth: int, lines: list[str]) -> None:
        indent = "  " * depth
        content = (block.content or "").replace("\n", "\n" + indent + "  ")
        lines.append(f"{indent}- {content}")
        lines.append(f"{indent}  id:: {block.id}")
        for key, value in block.properties.items():
            if key == "id":
                continue
            lines.append(f"{indent}  {key}:: {value}")
        for child in block.children:
            self._serialize_block(child, depth + 1, lines)

    def _apply_page_property(self, page: PageDocument, key: str, value: str) -> None:
        if key == "id":
            page.id = value
        elif key == "title":
            page.title = value
        elif key == "tags":
            page.tags = [t.strip() for t in value.split(",") if t.strip()]
        else:
            page.metadata[key] = value
