"""Wiki 知识编译引擎 — Ingest 编译 + 交叉引用 + Query 回存"""
import json
import logging
import re
import threading
import uuid
from datetime import datetime

from src.data.wiki_schema import (
    DEAD_LINK_REPAIR_PROMPT,
    INGEST_PROMPT,
    LINK_DISCOVERY_PROMPT,
    MERGE_PROMPT,
    QUERY_SAVE_PROMPT,
)
from src.services.db import Database
from src.services.llm import LLMService
from src.utils.config import Config

logger = logging.getLogger(__name__)
_compile_slot = threading.BoundedSemaphore(value=1)

# 匹配 wiki 页面 content 中的 [[标题]] 或 [[标题#锚点]] 引用
_WIKI_LINK_RE = re.compile(r"\[\[([^]\n#]+)(?:#([^]\n]+))?\]\]")


def _strip_pipe(ref_title: str) -> str:
    """去除 Wiki 链接中的管道符显示文本，如 [[目标|显示]] → 目标"""
    return ref_title.split("|")[0].strip()


def try_wiki_compile(knowledge_id: str):
    """尝试对已导入的知识条目执行 Wiki 编译（供 MCP/API 层共享调用）"""
    if not Config.get("wiki.enabled", False) or not Config.get("wiki.auto_compile", True):
        return
    if not _compile_slot.acquire(blocking=False):
        logger.info("Wiki compile skipped for %s: previous compile still running", knowledge_id)
        return

    def _run():
        try:
            WikiCompiler().ingest(knowledge_id)
        except Exception as e:
            logger.warning("Wiki compile failed for %s: %s", knowledge_id, e)
        finally:
            _compile_slot.release()

    thread = threading.Thread(
        target=_run,
        name=f"WikiCompile-{knowledge_id[:8]}",
        daemon=True,
    )
    thread.start()


def resolve_all_content_links() -> dict:
    """扫描所有 Wiki 页面的 content，解析 [[...]] 引用并创建 wiki_links。

    返回处理结果统计，供 MCP 工具或管理脚本调用。
    """
    pages = Database.list_wiki_pages(limit=500)
    if not pages:
        return {"scanned": 0, "links_created": 0, "dead_references": [], "dead_reference_count": 0}

    total_links = 0
    all_dead_refs = []
    WikiCompiler()

    for page in pages:
        page_id = page["id"]
        content = page.get("content", "") or ""
        if not content:
            continue

        seen_targets = set()
        for match in _WIKI_LINK_RE.finditer(content):
            ref_title = _strip_pipe(match.group(1).strip())
            if ref_title in seen_targets or ref_title == page.get("title", ""):
                continue
            seen_targets.add(ref_title)

            target = Database.get_wiki_page_by_title(ref_title)
            # 只匹配非 deleted 状态的页面，与 lint 判定一致
            if target and target["id"] != page_id and target.get("status") != "deleted":
                try:
                    Database.add_wiki_link(page_id, target["id"], "references", 1.0)
                    total_links += 1
                except Exception as e:
                    logger.warning("Failed to add content link: %s", e)
            elif not target or (target and target.get("status") == "deleted"):
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
        return [str(tag) for tag in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return [str(tag) for tag in parsed] if isinstance(parsed, list) else []
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

    def _generate_summary(self, title: str, content: str) -> str | None:
        """用 LLM 为已有内容生成 concept_summary"""
        if not content or len(content) < 20:
            return None
        prompt = (
            f"请为以下 Wiki 页面生成一段 50-100 字的摘要，"
            f"直接输出摘要内容，不要加标题、不要加引号：\n\n"
            f"标题：{title}\n\n内容：{content[:1000]}"
        )
        try:
            response = self._llm.chat([{"role": "user", "content": prompt}], silent=True)
            summary = response.strip()
            if summary and len(summary) >= 10:
                return summary[:200]
        except Exception as e:
            logger.warning("Failed to generate summary for [%s]: %s", title, e)
        return None

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

    def _clean_stale_source_ids(self, pages: list[dict]) -> dict:
        """清理 Wiki 页面 source_ids 中指向已删除 knowledge_items 的引用。

        与 lint 的 stale 检查对齐，自动修复该类问题。

        Args:
            pages: Wiki 页面列表

        Returns:
            {"cleaned": 清理的页面数, "source_ids_removed": 移除的 source_id 总数, "details": [...]}
        """
        cleaned = 0
        total_removed = 0
        details = []

        for page in pages:
            source_ids_raw = page.get("source_ids", "[]")
            try:
                source_ids = json.loads(source_ids_raw) if isinstance(source_ids_raw, str) else source_ids_raw
            except (json.JSONDecodeError, TypeError):
                continue

            if not source_ids:
                continue

            existing = Database.get_knowledge_batch(source_ids)
            deleted_ids = [sid for sid in source_ids if sid not in existing]

            if not deleted_ids:
                continue

            # 从 source_ids 中移除已删除的 ID
            new_source_ids = [sid for sid in source_ids if sid in existing]
            new_source_ids_json = json.dumps(new_source_ids, ensure_ascii=False)

            Database.update_wiki_page(page["id"], source_ids=new_source_ids_json)
            cleaned += 1
            total_removed += len(deleted_ids)
            details.append({
                "page_id": page["id"],
                "page_title": page["title"],
                "removed_ids": deleted_ids,
                "remaining_ids": len(new_source_ids),
            })
            logger.info("Cleaned stale source_ids for [%s]: removed %d, remaining %d",
                        page["title"], len(deleted_ids), len(new_source_ids))

        return {"cleaned": cleaned, "source_ids_removed": total_removed, "details": details}

    def save_answer(
        self,
        question: str,
        answer: str,
        source_ids: list[str] | None = None,
        auto_publish: bool | None = None,
        enhance: bool = True,
    ) -> str | None:
        """将问答保存为 Wiki 页面。

        BUG#10：auto_publish（None=沿用 Config 'wiki.auto_publish' 默认 True）；
                False 时创建为 draft，可走 submit_for_review 审核流。
        BUG#11：enhance=False 时跳过 LLM 增强，直接用原始 answer 存储
                （title 取 question 前 N 字，tags 空，concept_summary 空）。
        """
        min_len = Config.get("wiki.query_save_min_length", 100)
        if len(answer) < min_len:
            return None

        # BUG#11：enhance=False → 跳过 LLM，直接存原始 answer
        if not enhance:
            title = question.strip().replace("\n", " ")[:self.QUERY_SAVE_TITLE_TRUNCATE] or "未命名"
            result = {
                "title": title,
                "content": answer,
                "tags": [],
                "summary": "",
            }
        else:
            prompt = QUERY_SAVE_PROMPT.format(question=question, answer=answer)
            try:
                response = self._llm.chat([{"role": "user", "content": prompt}], silent=True)
            except Exception as e:
                logger.warning("Wiki save_answer LLM call failed: %s", e)
                return None

            result = self._parse_json_response(response)
            if not isinstance(result, dict) or not result.get("title"):
                return None

        # BUG#10：显式 auto_publish 优先于 Config 默认
        _auto = auto_publish if auto_publish is not None else Config.get("wiki.auto_publish", True)
        initial_status = "published" if _auto else "draft"

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
        return str(page["id"])

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
        return str(page["id"])

    def _update_existing_page(self, concept: dict, knowledge_id: str) -> str | None:
        page_id = concept.get("existing_page_id")
        if not page_id:
            return None
        existing = Database.get_wiki_page(page_id)
        if not existing:
            return None

        new_content = concept.get("merge_content", "")
        if not new_content:
            return str(page_id)

        prompt = MERGE_PROMPT.format(
            existing_title=existing["title"],
            existing_content=existing.get("content", "")[:self.MERGE_EXISTING_TRUNCATE],
            source_title=knowledge_id,
            new_content=new_content[:self.MERGE_NEW_TRUNCATE],
        )
        try:
            response = self._llm.chat([{"role": "user", "content": prompt}], silent=True)
            parsed_result = self._parse_json_response(response)
            result = parsed_result if isinstance(parsed_result, dict) else None
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
        return str(page_id)

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
            ref_title = _strip_pipe(match.group(1).strip())
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
                    return str(p["id"])
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
            selected = data.get(key)
            return selected if isinstance(selected, (dict, list)) else None
        return data if isinstance(data, (dict, list)) else None

    def repair_dead_references(self, max_pages: int = 50) -> dict:
        """LLM 驱动的死链修复 + stale source_ids 清理。

        扫描所有 Wiki 页面中的 [[...]] 死链，调用 LLM 分析上下文后
        选择修复策略：redirect（重定向到已有页面）、stub（创建占位页面）
        或 remove（移除引用标记）。
        同时清理 source_ids 中指向已删除 knowledge_items 的过时引用。

        Args:
            max_pages: 最多处理多少个含死链的页面（控制 LLM 成本）

        Returns:
            修复结果统计
        """
        pages = Database.list_wiki_pages(limit=500)
        if not pages:
            return {"status": "empty", "scanned": 0, "fixed": 0}

        all_titles = {p["title"] for p in pages}
        title_to_id = {p["title"]: p["id"] for p in pages}

        # 第零步：清理 stale source_ids（与 lint 的 stale 检查对齐）
        stale_fixed = self._clean_stale_source_ids(pages)

        # 第一步：收集所有含死链的页面
        pages_with_dead = []  # [(page, [dead_ref_titles])]
        for page in pages:
            content = page.get("content", "") or ""
            if not content:
                continue
            dead_refs = []
            seen = set()
            for match in _WIKI_LINK_RE.finditer(content):
                ref_title = _strip_pipe(match.group(1).strip())
                if ref_title not in seen and ref_title not in all_titles:
                    seen.add(ref_title)
                    dead_refs.append(ref_title)
            if dead_refs:
                pages_with_dead.append((page, dead_refs))

        if not pages_with_dead:
            return {"status": "clean", "scanned": len(pages),
                    "pages_with_dead_refs": 0, "fixed": 0}

        # 限制处理数量
        pages_with_dead = pages_with_dead[:max_pages]

        # 构建已有页面摘要（供 LLM 匹配）
        existing_pages_text = self._get_existing_pages_summary()

        total_redirects = 0
        total_stubs = 0
        total_removes = 0
        total_errors = 0
        fix_details = []

        # 配置
        Config.get("wiki.auto_publish", True)

        # 第二步：逐页面调用 LLM 修复
        for page, dead_refs in pages_with_dead:
            content = page.get("content", "") or ""
            source_title = page["title"]

            dead_refs_text = "\n".join(f"- [[{r}]]" for r in dead_refs)

            prompt = DEAD_LINK_REPAIR_PROMPT.format(
                source_title=source_title,
                source_content=content[:1500],
                dead_refs=dead_refs_text,
                existing_pages=existing_pages_text,
            )

            try:
                response = self._llm.chat(
                    [{"role": "user", "content": prompt}],
                    silent=True,
                )
                result = self._parse_json_response(response, "fixes")
            except Exception as e:
                logger.warning("LLM dead link repair failed for [%s]: %s",
                               source_title, e)
                total_errors += 1
                continue

            if not result:
                logger.warning("LLM returned no fixes for [%s]", source_title)
                total_errors += 1
                continue

            # 第三步：应用修复
            updated_content = content
            page_fixes = []

            for fix in result:
                dead_ref = fix.get("dead_ref", "").strip()
                action = fix.get("action", "remove")
                reason = fix.get("reason", "")

                if not dead_ref:
                    continue

                if action == "redirect":
                    target_title = fix.get("target_title", "").strip()
                    if target_title and target_title in all_titles:
                        # 替换 [[死链]] 为 [[正确标题]]
                        updated_content = updated_content.replace(
                            f"[[{dead_ref}]]", f"[[{target_title}]]")
                        # 创建 wiki_link
                        target_id = title_to_id.get(target_title)
                        if target_id and target_id != page["id"]:
                            try:
                                Database.add_wiki_link(
                                    page["id"], target_id, "references", 1.0)
                            except Exception:
                                pass
                        total_redirects += 1
                        page_fixes.append({
                            "dead_ref": dead_ref, "action": "redirect",
                            "target": target_title, "reason": reason,
                        })
                    else:
                        # redirect 目标不存在，降级为 remove
                        updated_content = updated_content.replace(
                            f"[[{dead_ref}]]", dead_ref)
                        total_removes += 1
                        page_fixes.append({
                            "dead_ref": dead_ref, "action": "remove",
                            "reason": f"redirect 目标不存在，降级移除: {reason}",
                        })

                elif action == "stub":
                    new_title = fix.get("new_title", dead_ref).strip()
                    summary = fix.get("summary", "")
                    tags = fix.get("tags", [])

                    if not summary:
                        # 没有摘要，降级为 remove
                        updated_content = updated_content.replace(
                            f"[[{dead_ref}]]", dead_ref)
                        total_removes += 1
                        page_fixes.append({
                            "dead_ref": dead_ref, "action": "remove",
                            "reason": "stub 缺少摘要，降级移除",
                        })
                        continue

                    # 创建占位页面
                    stub_page = {
                        "id": str(uuid.uuid4()),
                        "title": new_title,
                        "content": (
                            f"> 此页面为自动创建的占位页面，内容待补充。\n\n"
                            f"## {new_title}\n\n{summary}\n\n"
                            f"---\n*由死链修复工具自动创建于 "
                            f"{datetime.now().strftime('%Y-%m-%d')}*"
                        ),
                        "source_ids": json.dumps(
                            [page["id"]], ensure_ascii=False),
                        "tags": json.dumps(tags, ensure_ascii=False),
                        "concept_summary": summary,
                        "status": "draft",  # 占位页面始终为 draft
                        "lint_score": 0.5,
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat(),
                    }
                    Database.insert_wiki_page(stub_page)

                    # 替换内容中的引用（如果标题变了）
                    if new_title != dead_ref:
                        updated_content = updated_content.replace(
                            f"[[{dead_ref}]]", f"[[{new_title}]]")

                    # 创建 wiki_link
                    try:
                        Database.add_wiki_link(
                            page["id"], stub_page["id"], "references", 1.0)
                    except Exception:
                        pass

                    # 更新全局标题集合（避免后续修复重复创建）
                    all_titles.add(new_title)
                    title_to_id[new_title] = stub_page["id"]

                    total_stubs += 1
                    page_fixes.append({
                        "dead_ref": dead_ref, "action": "stub",
                        "new_page_id": stub_page["id"],
                        "new_title": new_title, "reason": reason,
                    })

                else:  # remove
                    updated_content = updated_content.replace(
                        f"[[{dead_ref}]]", dead_ref)
                    total_removes += 1
                    page_fixes.append({
                        "dead_ref": dead_ref, "action": "remove",
                        "reason": reason,
                    })

            # 更新页面内容
            if updated_content != content:
                Database.update_wiki_page(page["id"], content=updated_content)

            if page_fixes:
                fix_details.append({
                    "source_page": source_title,
                    "fixes": page_fixes,
                })

        # 记录操作日志
        summary = {
            "pages_processed": len(pages_with_dead),
            "redirects": total_redirects,
            "stubs_created": total_stubs,
            "removed": total_removes,
            "errors": total_errors,
            "stale_cleaned": stale_fixed["cleaned"],
            "stale_source_ids_removed": stale_fixed["source_ids_removed"],
        }
        Database.insert_wiki_op("repair_dead_references", "", summary)

        return {
            "status": "success",
            "scanned": len(pages),
            "pages_with_dead_refs": len(pages_with_dead),
            **summary,
            "details": fix_details,
            "stale_details": stale_fixed.get("details", []),
        }
