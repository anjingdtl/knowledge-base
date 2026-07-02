"""wiki-first source summary 编译器(规则模板,零 LLM)。

从 knowledge 条目抽取标题/首段/关键实体,生成 ``wiki/sources/<slug>.md``。
幂等:同 source_hash 覆盖,不产生重复。
时间戳由调用方传入,内部不取系统时间(可复现)。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from src.services.db import Database
from src.services.wiki_slug import resolve_slug, write_markdown
from src.utils.config import Config

logger = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 500
KEY_ENTITIES_LIMIT = 10
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")
_WORD_RE = re.compile(r"[\w一-鿿]+")
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "for", "on", "with",
    "is", "are", "be", "as", "at", "by", "this", "that",
}


class WikiSourceCompiler:
    """单源摘要页编译器(规则驱动,无 LLM 调用)。"""

    def compile(self, knowledge_id: str, ingested_at: str) -> dict:
        """为 knowledge 生成 source summary 页。

        Returns:
            ``{"status","path","slug","key_entities","summary"}``;
            knowledge 不存在时 ``{"status":"not_found"}``。
        """
        item = Database.get_knowledge(knowledge_id)
        if not item:
            return {"status": "not_found"}

        wiki_dir = Config.get("knowledge_workflow.wiki_dir", "wiki")
        sources_dir = Path(
            Config.get("knowledge_workflow.source_summary_dir", f"{wiki_dir}/sources")
        )
        sources_dir.mkdir(parents=True, exist_ok=True)

        title = item.get("title") or "untitled"
        content = item.get("content") or ""
        source_hash = item.get("content_hash") or ""

        slug, target = resolve_slug(sources_dir, title, source_hash)
        summary = self._build_summary(content)
        entities = self._extract_key_entities(content, title)

        frontmatter = {
            "title": title,
            "source_path": item.get("source_path", ""),
            "file_type": item.get("file_type", ""),
            "source_hash": source_hash,
            "ingested_at": ingested_at,
            "key_entities": entities,
            "knowledge_id": knowledge_id,
        }
        body = self._render_body(frontmatter, summary)
        write_markdown(target, frontmatter, body)
        logger.info("source summary compiled: %s (kid=%s)", target, knowledge_id)
        return {
            "status": "compiled",
            "path": str(target),
            "slug": slug,
            "key_entities": entities,
            "summary": summary,
        }

    @staticmethod
    def _build_summary(content: str) -> str:
        """首段 + 标题路径精炼,截断 ≤500 字。"""
        if not content:
            return ""
        lines = content.splitlines()
        heading_path = [
            ln.strip().lstrip("#").strip() for ln in lines if ln.strip().startswith("#")
        ]
        first_para = ""
        for ln in lines:
            stripped = ln.strip()
            if stripped and not stripped.startswith("#"):
                first_para = stripped
                break
        parts: list[str] = []
        if heading_path:
            parts.append(" / ".join(heading_path[:5]))
        if first_para:
            parts.append(first_para)
        summary = "\n\n".join(parts) if parts else content[:SUMMARY_MAX_CHARS]
        return summary[:SUMMARY_MAX_CHARS]

    @staticmethod
    def _extract_key_entities(content: str, title: str) -> list[str]:
        """规则抽取专名/缩略词(零 LLM)。"""
        entities: list[str] = []
        for acr in _ACRONYM_RE.findall(content or ""):
            if acr not in entities:
                entities.append(acr)
        for w in _WORD_RE.findall(title or ""):
            wl = w.lower()
            if len(wl) > 1 and wl not in _STOPWORDS and w not in entities:
                entities.append(w)
        return entities[:KEY_ENTITIES_LIMIT]

    @staticmethod
    def _render_body(frontmatter: dict, summary: str) -> str:
        lines = [f"# {frontmatter['title']}", ""]
        lines.append(f"**Source:** `{frontmatter['source_path']}`  ")
        lines.append(f"**Type:** {frontmatter['file_type']}  ")
        lines.append(f"**Ingested:** {frontmatter['ingested_at']}")
        lines.append("")
        if frontmatter.get("key_entities"):
            lines.append("## Key entities")
            lines.append("")
            lines.append(", ".join(f"[[{e}]]" for e in frontmatter["key_entities"]))
            lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(summary or "(empty)")
        return "\n".join(lines)
