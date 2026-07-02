"""``wiki/index.md`` 生成器:按 page type 分组聚合所有 wiki 页。全量重建。"""
from __future__ import annotations

from pathlib import Path

from src.services.wiki_slug import read_frontmatter, write_markdown
from src.utils.config import Config

PAGE_TYPE_DIRS = ["sources", "entities", "concepts", "comparisons", "syntheses"]
PAGE_TYPE_LABELS = {
    "sources": "Sources",
    "entities": "Entities",
    "concepts": "Concepts",
    "comparisons": "Comparisons",
    "syntheses": "Syntheses",
}


class WikiIndexCompiler:
    def refresh(self) -> dict:
        """扫描 wiki 子目录,全量重建 ``wiki/index.md``。"""
        wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
        wiki_dir.mkdir(parents=True, exist_ok=True)
        sections: list[tuple[str, list[tuple[str, str]]]] = []
        total = 0
        for ptype in PAGE_TYPE_DIRS:
            label = PAGE_TYPE_LABELS[ptype]
            sub = wiki_dir / ptype
            entries: list[tuple[str, str]] = []
            if sub.is_dir():
                for md in sorted(sub.glob("*.md")):
                    fm = read_frontmatter(md)
                    title = fm.get("title") or md.stem
                    rel = md.relative_to(wiki_dir).as_posix()
                    entries.append((title, rel))
            total += len(entries)
            sections.append((label, entries))
        body = self._render(sections)
        index_path = wiki_dir / "index.md"
        write_markdown(index_path, {"generated": True}, body)
        return {"status": "compiled", "path": str(index_path), "page_count": total}

    @staticmethod
    def _render(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
        lines = ["# Wiki Index", ""]
        for label, entries in sections:
            lines.append(f"## {label}")
            lines.append("")
            if not entries:
                lines.append("_(none)_")
            else:
                for title, rel in entries:
                    lines.append(f"- [{title}]({rel})")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
