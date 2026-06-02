"""内容嵌入（Transclusion）— 支持在文本中引用其他块/页面内容

用法:
    from src.core.transclusion import resolve_transclusions

    text = "参见 {{embed:block:abc123}} 和 {{embed:wiki:概念名}}"
    resolved = resolve_transclusions(text)
    # → "参见 [嵌入的块内容] 和 [嵌入的Wiki页面内容]"
"""
import json
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 嵌入标记正则: {{embed:type:id}} 或 {{embed:type:id|显示文字}}
_EMBED_PATTERN = re.compile(r'\{\{embed:(block|wiki|knowledge):([^}|]+?)(?:\|([^}]*))?\}\}')


def resolve_transclusions(text: str, max_depth: int = 3, _depth: int = 0) -> str:
    """解析文本中的嵌入标记，替换为实际内容

    Args:
        text: 包含嵌入标记的文本
        max_depth: 最大递归深度（防止循环引用）
        _depth: 当前递归深度（内部使用）

    Returns:
        解析后的文本
    """
    if _depth >= max_depth:
        return text

    def _replace(match):
        embed_type = match.group(1)
        embed_id = match.group(2).strip()
        display_text = match.group(3)

        content = _fetch_content(embed_type, embed_id)
        if content is None:
            return match.group(0)  # 找不到就保留原始标记

        # 递归解析嵌入内容中的嵌入
        if _depth < max_depth - 1:
            content = resolve_transclusions(content, max_depth, _depth + 1)

        if display_text:
            return f"【{display_text}】\n{content}"
        return content

    return _EMBED_PATTERN.sub(_replace, text)


def _fetch_content(embed_type: str, embed_id: str) -> Optional[str]:
    """根据类型和 ID 获取嵌入内容"""
    from src.services.db import Database

    try:
        if embed_type == "block":
            # 从 blocks 表获取（精确匹配 ID）
            conn = Database.get_conn()
            row = conn.execute(
                "SELECT content FROM blocks WHERE id = ?",
                (embed_id,),
            ).fetchone()
            return row["content"] if row else None

        elif embed_type == "wiki":
            # 从 wiki_pages 表获取（支持按标题查找）
            page = Database.get_wiki_page(embed_id)
            if not page:
                page = Database.get_wiki_page_by_title(embed_id)
            if page:
                parts = []
                if page.get("concept_summary"):
                    parts.append(page["concept_summary"])
                if page.get("content"):
                    parts.append(page["content"][:500])
                return "\n".join(parts) if parts else None
            return None

        elif embed_type == "knowledge":
            # 从 knowledge_items 表获取
            item = Database.get_knowledge(embed_id)
            if item:
                return item.get("content", "")[:1000]
            return None

    except Exception as e:
        logger.warning("Failed to fetch embed content %s:%s: %s", embed_type, embed_id, e)
        return None


def find_embed_references(text: str) -> list[dict]:
    """提取文本中所有嵌入引用，返回引用列表"""
    refs = []
    for match in _EMBED_PATTERN.finditer(text):
        refs.append({
            "type": match.group(1),
            "id": match.group(2).strip(),
            "display": match.group(3),
            "full_match": match.group(0),
            "position": match.start(),
        })
    return refs
