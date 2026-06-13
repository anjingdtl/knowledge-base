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

        page_ids_set = {p["id"] for p in pages}
        title_map = {p["id"]: p["title"] for p in pages}
        {p["id"]: p for p in pages}

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

        # 5. 损坏链接 — 指向不存在的 wiki_pages
        for link in all_links:
            if link["source_page_id"] not in page_ids_set or link["target_page_id"] not in page_ids_set:
                report.findings.append(LintFinding(
                    severity="error", category="broken_link",
                    page_id=link.get("source_page_id", ""),
                    page_title=title_map.get(link.get("source_page_id", ""), "未知"),
                    message="链接指向不存在的页面",
                    detail={
                        "source": link.get("source_title", ""),
                        "target": link.get("target_title", ""),
                    },
                ))

        # 6. 内容死链 — content 中的 [[...]] 引用指向不存在的页面
        all_titles = {p["title"] for p in pages}
        {p["title"]: p["id"] for p in pages}
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
