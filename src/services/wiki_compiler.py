"""Wiki 知识编译引擎 — Ingest 编译 + 交叉引用 + Query 回存"""
import json
import re
import uuid
import logging
from datetime import datetime

from src.services.db import Database
from src.services.llm import LLMService
from src.utils.config import Config
from src.data.wiki_schema import INGEST_PROMPT, MERGE_PROMPT, LINK_DISCOVERY_PROMPT, QUERY_SAVE_PROMPT

logger = logging.getLogger(__name__)

# 匹配 wiki 页面 content 中的 [[标题]] 或 [[标题#锚点]] 引用
_WIKI_LINK_RE = re.compile(r"\[\[([^]\n#]+)(?:#([^]\n]+))?\]\]")


def try_wiki_compile(knowledge_id: str):
    """尝试对已导入的知识条目执行 Wiki 编译（供 MCP/API 层共享调用）"""
    if not Config.get("wiki.enabled", False) or not Config.get("wiki.auto_compile", True):
        return
    try:
        WikiCompiler().ingest(knowledge_id)
    except Exception as e:
        logger.warning("Wiki compile failed for %s: %s", knowledge_id, e)


def resolve_all_content_links() -> dict:
    """扫描所有 Wiki 页面的 content，解析 [[...]] 引用并创建 wiki_links。

    返回处理结果统计，供 MCP 工具或管理脚本调用。
    """
    pages = Database.list_wiki_pages(limit=500)
    if not pages:
        return {"scanned": 0, "links_created": 0, "dead_refs": []}

    total_links = 0
    all_dead_refs = []
    compiler = WikiCompiler()

    for page in pages:
        page_id = page["id"]
        content = page.get("content", "") or ""
        if not content:
            continue

        seen_targets = set()
        for match in _WIKI_LINK_RE.finditer(content):
            ref_title = match.group(1).strip()
            if ref_title in seen_targets or ref_title == page.get("title", ""):
                continue
            seen_targets.add(ref_title)

            target = Database.get_wiki_page_by_title(ref_title)
            if target and target["id"] != page_id:
                try:
                    Database.add_wiki_link(page_id, target["id"], "references", 1.0)
                    total_links += 1
                except Exception as e:
                    logger.warning("Failed to add content link: %s", e)
            elif not target:
                all_dead_refs.append({
                    "source_page_id": page_id,
                    "source_title": page["title"],
                    "missing_title": ref_title,
                })

    # 汇总死链
    unique_dead = {}
    for d in all_dead_refs:
        key = d["missing_title"]
        if key not in unique_dead:
            unique_dead[key] = {"missing_title": key, "referenced_by": []}
        unique_dead[key]["referenced_by"].append(d["source_title"])

    return {
        "scanned": len(pages),
        "links_created": total_links,
        "dead_references": list(unique_dead.values()),
        "dead_reference_count": len(unique_dead),
    }


def parse_tags(raw) -> list[str]:
    """解析 tags 字段（兼容 JSON 字符串和列表）"""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


class WikiCompiler:
    # 可配置的截断和限制常量
    MAX_CONCEPTS_PER_INGEST = 5
    INGEST_CONTENT_TRUNCATE = 3000
    MERGE_EXISTING_TRUNCATE = 2000
    MERGE_NEW_TRUNCATE = 1500
    LINK_DISCOVERY_LIMIT = 5
    LINK_CANDIDATE_LIMIT = 20
    EXISTING_PAGES_LIMIT = 50
    EXISTING_PAGES_SUMMARY_TRUNCATE = 60
    LINK_CANDIDATE_SUMMARY_TRUNCATE = 50
    QUERY_SAVE_TITLE_TRUNCATE = 100

    def __init__(self):
        self._llm = LLMService()

    def ingest(self, knowledge_id: str) -> dict:
        """将知识条目编译为 Wiki 页面，返回新创建/更新的页面 ID 列表"""
        if not Config.get("wiki.enabled", False):
            return {"created": [], "updated": [], "status": "skipped"}

        # 幂等保护：检查是否已有该 knowledge_id 的成功 ingest 记录
        ops = Database.list_wiki_ops(limit=200)
        for op in ops:
            if op.get("op_type") == "ingest" and op.get("target_id") == knowledge_id:
                return {"created": [], "updated": [], "status": "already_compiled"}

        item = Database.get_knowledge(knowledge_id)
        if not item:
            return {"created": [], "updated": [], "status": "not_found"}

        existing_pages = self._get_existing_pages_summary()
        prompt = INGEST_PROMPT.format(
            title=item["title"],
            content=(item.get("content") or "")[:self.INGEST_CONTENT_TRUNCATE],
            existing_pages=existing_pages,
        )

        try:
            response = self._llm.chat(
                [{"role": "user", "content": prompt}],
                silent=True,
            )
            concepts = self._parse_json_response(response, "concepts")
        except Exception as e:
            logger.warning("Wiki ingest LLM call failed for %s: %s", knowledge_id, e)
            return {"created": [], "updated": [], "status": "error", "error": str(e)}

        if not concepts:
            return {"created": [], "updated": [], "status": "no_concepts"}

        # 根据配置决定初始状态
        auto_publish = Config.get("wiki.auto_publish", True)
        initial_status = "published" if auto_publish else "draft"

        created_ids = []
        updated_ids = []
        for concept in concepts[:self.MAX_CONCEPTS_PER_INGEST]:
            try:
                if concept.get("action") == "update" and concept.get("existing_page_id"):
                    pid = self._update_existing_page(concept, knowledge_id)
                    if pid:
                        updated_ids.append(pid)
                else:
                    pid = self._create_new_page(concept, knowledge_id, initial_status)
                    if pid:
                        created_ids.append(pid)
            except Exception as e:
                logger.warning("Failed to process concept %s: %s", concept.get("title"), e)

        if (created_ids or updated_ids) and Config.get("wiki.auto_link", False):
            all_pids = created_ids + updated_ids
            for pid in all_pids:
                self._discover_links(pid)

        # 后处理：解析 content 中的 [[...]] 引用，创建 wiki_links
        all_affected_ids = created_ids + updated_ids
        for pid in all_affected_ids:
            self._resolve_content_links(pid)

        Database.insert_wiki_op("ingest", knowledge_id, {
            "title": item["title"],
            "pages_created": len(created_ids),
            "pages_updated": len(updated_ids),
        })
        return {"created": created_ids, "updated": updated_ids, "status": "success"}

    def save_answer(self, question: str, answer: str, source_ids: list[str] | None = None) -> str | None:
        """将问答保存为 Wiki 页面"""
        min_len = Config.get("wiki.query_save_min_length", 100)
        if len(answer) < min_len:
            return None

        prompt = QUERY_SAVE_PROMPT.format(question=question, answer=answer)
        try:
            response = self._llm.chat([{"role": "user", "content": prompt}], silent=True)
        except Exception as e:
            logger.warning("Wiki save_answer LLM call failed: %s", e)
            return None

        result = self._parse_json_response(response)
        if not result or not result.get("title"):
            return None

        # 根据配置决定初始状态
        auto_publish = Config.get("wiki.auto_publish", True)
        initial_status = "published" if auto_publish else "draft"

        page = {
            "id": str(uuid.uuid4()),
            "title": result["title"],
            "content": result.get("content", ""),
            "source_ids": json.dumps(source_ids or [], ensure_ascii=False),
            "tags": json.dumps(result.get("tags", []), ensure_ascii=False),
            "concept_summary": result.get("summary", ""),
            "status": initial_status,
            "lint_score": 1.0,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        Database.insert_wiki_page(page)
        Database.insert_wiki_op("query_save", page["id"], {
            "question": question[:self.QUERY_SAVE_TITLE_TRUNCATE],
            "title": result["title"],
        })
        return page["id"]

    def _get_existing_pages_summary(self) -> str:
        pages = Database.list_wiki_pages(status="active", limit=self.EXISTING_PAGES_LIMIT)
        if not pages:
            return "（暂无 Wiki 页面）"
        lines = []
        for p in pages:
            lines.append(f"- [{p['id'][:8]}] {p['title']}: {p.get('concept_summary', '')[:self.EXISTING_PAGES_SUMMARY_TRUNCATE]}")
        return "\n".join(lines)

    def _create_new_page(self, concept: dict, knowledge_id: str, initial_status: str = "draft") -> str | None:
        title = concept.get("title", "").strip()
        if not title:
            return None
        existing = Database.get_wiki_page_by_title(title)
        if existing:
            return self._update_existing_page(
                {"existing_page_id": existing["id"], "merge_content": concept.get("content", ""),
                 "tags": concept.get("tags", []), "summary": concept.get("summary", "")},
                knowledge_id,
            )

        tags = concept.get("tags", [])
        page = {
            "id": str(uuid.uuid4()),
            "title": title,
            "content": concept.get("content", ""),
            "source_ids": json.dumps([knowledge_id], ensure_ascii=False),
            "tags": json.dumps(tags, ensure_ascii=False),
            "concept_summary": concept.get("summary", ""),
            "status": initial_status,
            "lint_score": 1.0,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        Database.insert_wiki_page(page)
        self._auto_link_by_tags(page["id"], tags)
        return page["id"]

    def _update_existing_page(self, concept: dict, knowledge_id: str) -> str | None:
        page_id = concept.get("existing_page_id")
        if not page_id:
            return None
        existing = Database.get_wiki_page(page_id)
        if not existing:
            return None

        new_content = concept.get("merge_content", "")
        if not new_content:
            return page_id

        prompt = MERGE_PROMPT.format(
            existing_title=existing["title"],
            existing_content=existing.get("content", "")[:self.MERGE_EXISTING_TRUNCATE],
            source_title=knowledge_id,
            new_content=new_content[:self.MERGE_NEW_TRUNCATE],
        )
        try:
            response = self._llm.chat([{"role": "user", "content": prompt}], silent=True)
            result = self._parse_json_response(response)
        except Exception as e:
            logger.warning("Wiki merge LLM call failed: %s", e)
            result = None

        if result:
            updates = {}
            if result.get("content"):
                updates["content"] = result["content"]
            if result.get("summary"):
                updates["concept_summary"] = result["summary"]
            if result.get("tags"):
                updates["tags"] = json.dumps(result["tags"], ensure_ascii=False)
            if updates:
                source_ids = json.loads(existing.get("source_ids", "[]"))
                if knowledge_id not in source_ids:
                    source_ids.append(knowledge_id)
                updates["source_ids"] = json.dumps(source_ids, ensure_ascii=False)
                Database.update_wiki_page(page_id, **updates)
        return page_id

    def _auto_link_by_tags(self, page_id: str, tags: list[str]):
        """基于标签重叠自动创建交叉引用（零 LLM 成本）"""
        if len(tags) < 2:
            return
        all_pages = Database.list_wiki_pages(status="active", limit=200)
        for other in all_pages:
            if other["id"] == page_id:
                continue
            other_tags = json.loads(other.get("tags", "[]"))
            overlap = len(set(tags) & set(other_tags))
            if overlap >= 2:
                common = len(set(tags) & set(other_tags))
                union = len(set(tags) | set(other_tags))
                weight = common / max(union, 1)
                Database.add_wiki_link(page_id, other["id"], "related", round(weight, 2))
                Database.add_wiki_link(other["id"], page_id, "related", round(weight, 2))

    def _discover_links(self, page_id: str):
        """LLM 驱动的语义关联发现"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return
        candidates = Database.list_wiki_pages(status="active", limit=self.LINK_CANDIDATE_LIMIT)
        candidate_lines = []
        for c in candidates:
            if c["id"] == page_id:
                continue
            candidate_lines.append(f"- [{c['id'][:8]}] {c['title']}: {c.get('concept_summary', '')[:self.LINK_CANDIDATE_SUMMARY_TRUNCATE]}")
        if not candidate_lines:
            return

        prompt = LINK_DISCOVERY_PROMPT.format(
            new_title=page["title"],
            new_summary=page.get("concept_summary", ""),
            candidate_pages="\n".join(candidate_lines),
        )
        try:
            response = self._llm.chat([{"role": "user", "content": prompt}], silent=True)
            result = self._parse_json_response(response, "links")
        except Exception:
            return

        if not result:
            return
        for link in result[:self.LINK_DISCOVERY_LIMIT]:
            target_id = link.get("target_page_id")
            if not target_id:
                continue
            full_id = self._resolve_page_id(target_id)
            if full_id and full_id != page_id:
                Database.add_wiki_link(page_id, full_id, link.get("link_type", "related"), 1.0)

    def _resolve_content_links(self, page_id: str):
        """扫描页面 content 中的 [[...]] 引用，创建 wiki_links 记录。

        对于引用了不存在页面的链接，记录日志但不创建占位页面
        （占位页面应由 wiki_lint 报告后人工决定是否创建）。
        """
        page = Database.get_wiki_page(page_id)
        if not page:
            return
        content = page.get("content", "") or ""
        if not content:
            return

        seen_targets = set()
        for match in _WIKI_LINK_RE.finditer(content):
            ref_title = match.group(1).strip()
            if ref_title in seen_targets:
                continue
            seen_targets.add(ref_title)

            # 跳过自引用
            if ref_title == page.get("title", ""):
                continue

            target = Database.get_wiki_page_by_title(ref_title)
            if target and target["id"] != page_id:
                try:
                    Database.add_wiki_link(page_id, target["id"], "references", 1.0)
                except Exception as e:
                    logger.warning("Failed to add content link %s -> %s: %s",
                                   page["title"], ref_title, e)
            elif not target:
                logger.info("Dead reference in [%s]: [[%s]] — target page not found",
                            page["title"], ref_title)

    def _resolve_page_id(self, partial_or_full: str) -> str | None:
        """解析可能是截断的页面 ID"""
        page = Database.get_wiki_page(partial_or_full)
        if page:
            return partial_or_full
        if len(partial_or_full) == 8:
            pages = Database.list_wiki_pages(limit=500)
            for p in pages:
                if p["id"].startswith(partial_or_full):
                    return p["id"]
        return None

    def _parse_json_response(self, response: str, key: str | None = None) -> dict | list | None:
        """从 LLM 响应中提取 JSON"""
        text = response.strip()
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            text = text[start:end].strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if key and isinstance(data, dict):
            return data.get(key)
        return data
