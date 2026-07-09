"""wiki-first 实体/概念页 LLM 更新器。

根据 source summary 的 key_entities,用 LLM 生成实体/概念组织建议。
硬上限:``wiki.max_llm_calls_per_ingest``(默认 3)。矛盾显式标注。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.services.llm import LLMService
from src.services.wiki_slug import slugify
from src.utils.config import Config

logger = logging.getLogger(__name__)

ENTITY_PROMPT_TEMPLATE = """\
你正在维护一个 wiki 知识库的实体页。基于新源摘要更新实体信息。

实体名: {entity}
实体类型: {kind}
新源标题: {source_title}
新源摘要:
{source_summary}
新源关键实体: {key_entities}

已有实体页内容(空则新建):
{existing_content}

仅输出 JSON(无其它文字):
{{
  "action": "create" | "update",
  "summary": "该实体 50-150 字描述",
  "facts": ["从新源提取的事实条目"],
  "contradictions": ["与已有内容矛盾处;无则空列表"]
}}
"""


class WikiEntityUpdater:
    """LLM 驱动的实体/概念组织建议服务。"""

    def __init__(self, llm: LLMService | None = None):
        self._llm = llm or LLMService()

    def update(self, knowledge_id: str, source_summary: dict, ingested_at: str) -> dict:
        """为新源涉及的实体/概念生成组织建议,不直接写 canonical 文件。"""
        max_calls = int(Config.get("wiki.max_llm_calls_per_ingest", 3))
        entities = list(source_summary.get("key_entities", []))[:max_calls]
        result: dict[str, Any] = {
            "entities_created": 0,
            "concepts_created": 0,
            "suggestions": [],
            "llm_calls": 0,
            "contradictions": [],
        }
        if not entities:
            return result

        wiki_dir = Config.get("knowledge_workflow.wiki_dir", "wiki")
        entity_dir = Path(Config.get("knowledge_workflow.entity_dir", f"{wiki_dir}/entities"))
        concept_dir = Path(Config.get("knowledge_workflow.concept_dir", f"{wiki_dir}/concepts"))
        source_title = source_summary.get("title", "")
        source_summary_text = source_summary.get("summary", "")

        for entity in entities:
            if result["llm_calls"] >= max_calls:
                logger.warning("entity update hit max_llm_calls (%d), truncating", max_calls)
                break
            kind = self._classify(entity)
            target_dir = entity_dir if kind == "entity" else concept_dir
            existing_path = target_dir / f"{slugify(entity)}.md"
            existing_content = ""
            if existing_path.exists():
                existing_content = self._strip_frontmatter(
                    existing_path.read_text(encoding="utf-8")
                )
            try:
                resp = self._llm.chat(
                    [{"role": "user", "content": ENTITY_PROMPT_TEMPLATE.format(
                        entity=entity,
                        kind=kind,
                        source_title=source_title,
                        source_summary=source_summary_text[:400],
                        key_entities=", ".join(entities),
                        existing_content=existing_content[:1000],
                    )}],
                    silent=True,
                )
            except Exception as e:
                logger.warning("entity LLM call failed for %s: %s", entity, e)
                continue
            result["llm_calls"] += 1
            parsed = self._parse_json(resp)
            if not parsed:
                continue
            if parsed.get("contradictions"):
                result["contradictions"].extend(parsed["contradictions"])
            result["suggestions"].append(
                self._build_entity_suggestion(
                    target_dir, entity, kind, parsed, knowledge_id, ingested_at,
                    bool(existing_content),
                )
            )
        return result

    @staticmethod
    def _classify(entity: str) -> str:
        """简单分类:全大写缩略词 → concept;其余 → entity。"""
        if entity.isupper() and len(entity) <= 6:
            return "concept"
        return "entity"

    @staticmethod
    def _parse_json(response: str) -> dict | None:
        if not response:
            return None
        start = response.find("{")
        end = response.rfind("}")
        if start < 0 or end < 0:
            return None
        try:
            data = json.loads(response[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text.strip()

    @staticmethod
    def _build_entity_suggestion(
        target_dir: Path, entity: str, kind: str, parsed: dict,
        knowledge_id: str, ingested_at: str, is_update: bool,
    ) -> dict:
        slug = slugify(entity)
        body_lines = [f"# {entity}", "", parsed.get("summary", ""), ""]
        if parsed.get("facts"):
            body_lines.append("## Facts")
            body_lines.append("")
            for f in parsed["facts"]:
                body_lines.append(f"- {f}")
            body_lines.append("")
        if parsed.get("contradictions"):
            body_lines.append("## Contradictions")
            body_lines.append("")
            for c in parsed["contradictions"]:
                body_lines.append(f"> [CONTRADICTION] {c}")
            body_lines.append("")
        frontmatter = {
            "title": entity,
            "kind": kind,
            "knowledge_id": knowledge_id,
            "source_ids": [knowledge_id],
            "ingested_at": ingested_at,
            "updated": is_update,
        }
        return {
            "entity": entity,
            "kind": kind,
            "slug": slug,
            "suggested_path": str(target_dir / f"{slug}.md"),
            "frontmatter": frontmatter,
            "body": "\n".join(body_lines),
            "summary": parsed.get("summary", ""),
            "facts": list(parsed.get("facts") or []),
            "contradictions": list(parsed.get("contradictions") or []),
            "knowledge_id": knowledge_id,
            "source_ids": [knowledge_id],
            "ingested_at": ingested_at,
            "updated": is_update,
        }
