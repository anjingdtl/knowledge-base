"""Wiki 知识库健康检查引擎 — 孤立页面、过时信息、损坏链接等"""
import json
import logging
import re
from dataclasses import dataclass, field

from src.services.db import Database
from src.utils.config import Config

logger = logging.getLogger(__name__)

# 匹配 wiki 页面 content 中的 [[标题]] 或 [[标题#锚点]] 引用
_WIKI_LINK_RE = re.compile(r"\[\[([^]\n#]+)(?:#([^]\n]+))?\]\]")


@dataclass
class LintFinding:
    severity: str          # error | warning | info
    category: str          # orphan | stale | empty | duplicate | broken_link | dead_reference | contradiction
    page_id: str
    page_title: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class LintReport:
    findings: list[LintFinding] = field(default_factory=list)
    total_pages: int = 0
    healthy_pages: int = 0
    score: float = 1.0

    def to_dict(self) -> dict:
        return {
            "total_pages": self.total_pages,
            "healthy_pages": self.healthy_pages,
            "score": round(self.score, 2),
            "findings": [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "page_id": f.page_id,
                    "page_title": f.page_title,
                    "message": f.message,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
        }


class WikiLint:
    def run(self) -> dict:
        pages = Database.list_wiki_pages(limit=500)
        if not pages:
            return LintReport(total_pages=0, healthy_pages=0, score=1.0).to_dict()

        report = LintReport(total_pages=len(pages))
        all_links = Database.get_all_wiki_links()
        linked_page_ids = set()
        for link in all_links:
            linked_page_ids.add(link["source_page_id"])
            linked_page_ids.add(link["target_page_id"])

        for page in pages:
            findings_for_page = []

            # 1. 孤立页面 — 没有任何链接
            if page["id"] not in linked_page_ids:
                findings_for_page.append(LintFinding(
                    severity="warning", category="orphan",
                    page_id=page["id"], page_title=page["title"],
                    message="页面没有任何交叉引用链接",
                ))

            # 2. 过时页面 — source_ids 指向已删除的 knowledge_items
            source_ids = json.loads(page.get("source_ids", "[]"))
            if source_ids:
                existing = Database.get_knowledge_batch(source_ids)
                deleted = [sid for sid in source_ids if sid not in existing]
                if deleted:
                    findings_for_page.append(LintFinding(
                        severity="warning", category="stale",
                        page_id=page["id"], page_title=page["title"],
                        message=f"有 {len(deleted)} 个来源条目已被删除",
                        detail={"deleted_source_ids": deleted},
                    ))

            # 3. 内容空洞
            if not page.get("concept_summary") or not page.get("content"):
                findings_for_page.append(LintFinding(
                    severity="info", category="empty",
                    page_id=page["id"], page_title=page["title"],
                    message="页面摘要或内容为空",
                ))

            report.findings.extend(findings_for_page)

            if not findings_for_page:
                report.healthy_pages += 1

        # 4. 重复页面 — 相同标题
        title_counts: dict[str, list[str]] = {}
        for page in pages:
            title_counts.setdefault(page["title"], []).append(page["id"])
        for title, ids in title_counts.items():
            if len(ids) > 1:
                report.findings.append(LintFinding(
                    severity="warning", category="duplicate",
                    page_id=ids[0], page_title=title,
                    message=f"存在 {len(ids)} 个同名页面",
                    detail={"page_ids": ids},
                ))

        # 5. 损坏链接 — wiki_links 中物理悬空的记录(source/target 已被 purge 删除但链接残留)
        #    status=deleted 的软删页面物理仍存在,不算悬空,不在此列。
        dangling_links = Database.get_dangling_wiki_links()
        for link in dangling_links:
            src_id = link.get("source_page_id", "")
            tgt_id = link.get("target_page_id", "")
            src_title = link.get("source_title")
            tgt_title = link.get("target_title")
            report.findings.append(LintFinding(
                severity="error", category="broken_link",
                page_id=src_id,
                page_title=src_title or "未知",
                message="链接指向不存在的页面",
                detail={
                    "source": src_title or f"(已删除页面 {src_id[:8]})",
                    "target": tgt_title or f"(已删除页面 {tgt_id[:8]})",
                },
            ))

        # 6. 内容死链 — content 中的 [[...]] 引用指向不存在的页面
        all_titles = {p["title"] for p in pages}
        for page in pages:
            content = page.get("content", "") or ""
            dead_refs = []
            for match in _WIKI_LINK_RE.finditer(content):
                ref_title = match.group(1).strip()
                if ref_title not in all_titles:
                    dead_refs.append(ref_title)
            if dead_refs:
                # 去重但保留顺序
                seen = set()
                unique_dead = []
                for ref in dead_refs:
                    if ref not in seen:
                        seen.add(ref)
                        unique_dead.append(ref)
                report.findings.append(LintFinding(
                    severity="error", category="dead_reference",
                    page_id=page["id"], page_title=page["title"],
                    message=f"内容中有 {len(unique_dead)} 个引用指向不存在的页面: {', '.join(unique_dead[:5])}",
                    detail={"missing_titles": unique_dead},
                ))

        # 7. 矛盾检测（可选，高成本）
        if Config.get("wiki.lint_contradictions", False):
            self._check_contradictions(pages, report, all_links)

        # 计算总分
        if report.total_pages > 0:
            report.score = report.healthy_pages / report.total_pages

        # 更新各页面的 lint_score
        page_scores: dict[str, list[float]] = {p["id"]: [] for p in pages}
        base_score = 1.0
        for f in report.findings:
            if f.page_id in page_scores:
                if f.severity == "error":
                    page_scores[f.page_id].append(-0.3)
                elif f.severity == "warning":
                    page_scores[f.page_id].append(-0.1)
                else:
                    page_scores[f.page_id].append(-0.05)
        for pid, penalties in page_scores.items():
            score = max(0.0, base_score + sum(penalties))
            try:
                Database.update_wiki_page(pid, lint_score=round(score, 2))
            except Exception as e:
                logger.warning("Failed to update lint_score for %s: %s", pid, e)

        Database.insert_wiki_op("lint", "", {
            "total_pages": report.total_pages,
            "findings_count": len(report.findings),
            "score": round(report.score, 2),
        })
        return report.to_dict()

    def scan_complex_issues(self) -> dict:
        """扫描四类复杂问题：orphan/empty/duplicate/contradiction，返回详细结果。

        同时返回已有 complex_anomaly 标记的页面（待修复）。
        """
        pages = Database.list_wiki_pages(limit=500)
        if not pages:
            return {"scanned": 0, "issues": [], "pre_marked": []}

        all_links = Database.get_all_wiki_links()
        linked_page_ids = set()
        for link in all_links:
            linked_page_ids.add(link["source_page_id"])
            linked_page_ids.add(link["target_page_id"])

        issues = []

        for page in pages:
            categories = []

            # 1. orphan
            if page["id"] not in linked_page_ids:
                categories.append("orphan")

            # 2. empty
            if not page.get("concept_summary") or not page.get("content"):
                categories.append("empty")

            if categories:
                issues.append({
                    "page_id": page["id"],
                    "page_title": page["title"],
                    "categories": categories,
                    "status": page.get("status", ""),
                })

        # 3. duplicate
        title_counts: dict[str, list[str]] = {}
        for page in pages:
            title_counts.setdefault(page["title"], []).append(page["id"])
        for title, ids in title_counts.items():
            if len(ids) > 1:
                for pid in ids:
                    issues.append({
                        "page_id": pid,
                        "page_title": title,
                        "categories": ["duplicate"],
                        "duplicate_count": len(ids),
                        "duplicate_ids": ids,
                    })

        # 4. contradiction（可选，需配置开启）
        if Config.get("wiki.lint_contradictions", False):
            self._check_contradictions(pages, LintReport(), all_links)
            # contradiction 检测成本高，此处简化：只扫描标记

        # 已标记 complex_anomaly 的页面
        pre_marked = []
        for page in pages:
            anomaly = page.get("complex_anomaly", "")
            if anomaly:
                pre_marked.append({
                    "page_id": page["id"],
                    "page_title": page["title"],
                    "anomaly_types": anomaly,
                })

        return {
            "scanned": len(pages),
            "total_issues": len(issues),
            "issues": issues,
            "pre_marked": pre_marked,
        }

    @staticmethod
    def mark_complex_anomaly(page_id: str, categories: list[str]) -> None:
        """为页面标记复杂异常（不修复）"""
        anomaly_str = ",".join(categories)
        Database.update_wiki_page(page_id, complex_anomaly=anomaly_str)

    @staticmethod
    def clear_complex_anomaly(page_id: str) -> None:
        """清除页面的复杂异常标记"""
        Database.update_wiki_page(page_id, complex_anomaly="")

    def repair_complex_issues(self, issues: list[dict] | None = None) -> dict:
        """修复复杂问题：orphan/empty/duplicate

        - orphan: 尝试通过标题相似度创建 wiki_link 连接相关页面
        - empty: 用 LLM 补写 concept_summary 或 content
        - duplicate: 保留最新版本，将旧版本标记为 deprecated

        Args:
            issues: 指定修复的问题列表（None 则重新扫描）

        Returns:
            修复结果统计
        """
        if issues is None:
            scan_result = self.scan_complex_issues()
            issues = scan_result.get("issues", [])

        if not issues:
            return {"status": "clean", "scanned": 0, "fixed": 0}

        pages = Database.list_wiki_pages(limit=500)
        page_map = {p["id"]: p for p in pages}
        all_titles = {p["title"] for p in pages}
        title_to_ids: dict[str, list[str]] = {}
        for p in pages:
            title_to_ids.setdefault(p["title"], []).append(p["id"])

        orphan_fixed = 0
        empty_fixed = 0
        duplicate_fixed = 0
        errors = 0
        details = []

        for issue in issues:
            pid = issue["page_id"]
            cats = issue.get("categories", [])
            page = page_map.get(pid)
            if not page:
                continue

            for cat in cats:
                try:
                    if cat == "orphan":
                        # 找标题关键词最相似的页面，创建 wiki_link
                        title = page["title"]
                        best_match = self._find_similar_page(pid, title, page_map, all_titles)
                        if best_match:
                            Database.add_wiki_link(pid, best_match["id"], "related", 0.5)
                            orphan_fixed += 1
                            details.append({
                                "page_id": pid, "page_title": title, "category": "orphan",
                                "action": "linked", "target": best_match["title"],
                            })
                        else:
                            # 无法自动关联，标记复杂异常
                            WikiLint.mark_complex_anomaly(pid, ["orphan"])
                            details.append({
                                "page_id": pid, "page_title": title, "category": "orphan",
                                "action": "marked", "reason": "找不到相似页面可关联",
                            })

                    elif cat == "empty":
                        # 用 LLM 补写摘要/内容
                        title = page["title"]
                        content = page.get("content", "") or ""
                        summary = page.get("concept_summary", "") or ""

                        if not summary and not content:
                            # 从知识库中查找相关内容作为基础
                            self._fill_empty_page(page)
                            empty_fixed += 1
                            details.append({
                                "page_id": pid, "page_title": title, "category": "empty",
                                "action": "filled",
                            })
                        elif not summary and content:
                            # 从内容生成摘要
                            from src.services.wiki_compiler import WikiCompiler
                            compiler = WikiCompiler()
                            generated = compiler._generate_summary(title, content)
                            if generated:
                                Database.update_wiki_page(pid, concept_summary=generated)
                                empty_fixed += 1
                                details.append({
                                    "page_id": pid, "page_title": title, "category": "empty",
                                    "action": "summary_generated",
                                })
                            else:
                                WikiLint.mark_complex_anomaly(pid, ["empty"])
                        else:
                            WikiLint.mark_complex_anomaly(pid, ["empty"])

                    elif cat == "duplicate":
                        # 保留最新的，旧版本标记 deprecated
                        title = page["title"]
                        dup_ids = title_to_ids.get(title, [])
                        if len(dup_ids) > 1 and pid == dup_ids[0]:
                            # 只处理一次（由第一个 ID 负责去重）
                            # 找最新版本
                            sorted_ids = sorted(
                                dup_ids,
                                key=lambda i: page_map.get(i, {}).get("updated_at", ""),
                                reverse=True,
                            )
                            for old_id in sorted_ids[1:]:
                                old_page = page_map.get(old_id)
                                if old_page and old_page.get("status") not in ("deleted", "deprecated"):
                                    Database.update_wiki_page(old_id, status="deprecated")
                                    duplicate_fixed += 1
                                    details.append({
                                        "page_id": old_id,
                                        "page_title": title,
                                        "category": "duplicate",
                                        "action": "deprecated",
                                        "kept_id": sorted_ids[0],
                                    })

                    # 修复成功后清除异常标记
                    WikiLint.clear_complex_anomaly(pid)

                except Exception as e:
                    logger.error("Complex repair failed for [%s] %s: %s", cat, page["title"], e)
                    errors += 1

        Database.insert_wiki_op("repair_complex", "", {
            "orphan_fixed": orphan_fixed,
            "empty_fixed": empty_fixed,
            "duplicate_fixed": duplicate_fixed,
            "errors": errors,
        })

        return {
            "status": "success",
            "scanned": len(pages),
            "orphan_fixed": orphan_fixed,
            "empty_fixed": empty_fixed,
            "duplicate_fixed": duplicate_fixed,
            "errors": errors,
            "details": details,
        }

    def _find_similar_page(self, source_id: str, source_title: str,
                           page_map: dict, all_titles: set) -> dict | None:
        """通过标题关键词匹配找相似页面"""
        import re
        # 简单关键词匹配：提取标题中的核心词
        source_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', source_title))
        if not source_words:
            return None

        best_page = None
        best_score = 0
        for pid, page in page_map.items():
            if pid == source_id:
                continue
            if page.get("status") in ("deleted",):
                continue
            target_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', page["title"]))
            overlap = len(source_words & target_words)
            if overlap > best_score:
                best_score = overlap
                best_page = page

        # 至少 1 个词重叠才关联
        return best_page if best_score >= 1 else None

    def _fill_empty_page(self, page: dict) -> None:
        """为空页面从知识库检索并填充内容"""
        title = page["title"]
        from src.services.wiki_compiler import WikiCompiler
        compiler = WikiCompiler()

        # 从知识库搜索相关内容
        try:
            from src.core.container import create_container
            container = create_container()
            results = container.search_service.search(title, top_k=3)
            if results:
                # 用前 3 条结果拼接内容
                parts = []
                source_ids = []
                for r in results[:3]:
                    text = r.get("text_preview", r.get("text", ""))[:500]
                    if text:
                        parts.append(text)
                    kid = r.get("knowledge_id", "")
                    if kid:
                        source_ids.append(kid)

                content = "\n\n".join(parts)
                summary = content[:200].rsplit("。", 1)[0] + "。" if "。" in content[:200] else content[:200]

                import json
                Database.update_wiki_page(page["id"], content=content,
                                         concept_summary=summary,
                                         source_ids=json.dumps(source_ids, ensure_ascii=False))
            else:
                WikiLint.mark_complex_anomaly(page["id"], ["empty"])
        except Exception as e:
            logger.warning("Failed to fill empty page [%s]: %s", title, e)
            WikiLint.mark_complex_anomaly(page["id"], ["empty"])

    def _check_contradictions(self, pages: list[dict], report: LintReport,
                               all_links: list[dict] | None = None):
        """LLM 驱动的矛盾检测（成本受控，最多 20 对）"""
        from src.data.wiki_schema import LINT_PROMPT
        from src.services.llm import LLMService

        links = all_links or Database.get_all_wiki_links()
        related_pairs = set()
        for link in links:
            if link["link_type"] == "related":
                pair = tuple(sorted([link["source_page_id"], link["target_page_id"]]))
                related_pairs.add(pair)

        if not related_pairs:
            return

        page_map = {p["id"]: p for p in pages}
        checked = 0
        llm = LLMService()

        for src_id, tgt_id in list(related_pairs)[:20]:
            src = page_map.get(src_id)
            tgt = page_map.get(tgt_id)
            if not src or not tgt:
                continue

            prompt = LINT_PROMPT.format(
                title_a=src["title"],
                content_a=(src.get("content") or "")[:500],
                title_b=tgt["title"],
                content_b=(tgt.get("content") or "")[:500],
            )
            try:
                response = llm.chat([{"role": "user", "content": prompt}], silent=True)
                # 内联 JSON 解析，避免实例化 WikiCompiler
                text = response.strip()
                if "```json" in text:
                    text = text[text.find("```json") + 7:text.find("```", text.find("```json") + 7)].strip()
                elif "```" in text:
                    text = text[text.find("```") + 3:text.find("```", text.find("```") + 3)].strip()
                result = json.loads(text)
            except Exception:
                continue

            if result and result.get("has_contradiction"):
                for c in result.get("contradictions", []):
                    report.findings.append(LintFinding(
                        severity="warning", category="contradiction",
                        page_id=src_id, page_title=src["title"],
                        message=f"与「{tgt['title']}」存在矛盾: {c.get('topic', '')}",
                        detail={
                            "other_page_id": tgt_id,
                            "page_a_claim": c.get("page_a_claim", ""),
                            "page_b_claim": c.get("page_b_claim", ""),
                        },
                    ))
            checked += 1
