"""文件系统 wiki 健康检查引擎 — 扫描 wiki/*.md(spec Phase2 W4 Gap B)。

与 WikiLint(查 SQLite ``wiki_pages`` 表)正交:本引擎扫描 wiki-first 文件系统产物
(``wiki/<page_type>/*.md``),产出同构 LintReport,使 ``run_wiki_eval`` 的结构指标
对 wiki_first 项目真正生效(旧实现 ``WikiLint().run()`` 对纯文件系统项目恒返回
``total_pages=0``,结构指标全部失效)。

复用 ``wiki_slug.read_frontmatter`` / ``wiki_lint.{LintReport,LintFinding,
_WIKI_LINK_RE,_strip_pipe}``,保持 finding schema 与 SQLite 引擎一致。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from src.services.wiki_index_compiler import PAGE_TYPE_DIRS
from src.services.wiki_lint import (
    _WIKI_LINK_RE,
    LintFinding,
    LintReport,
    _strip_pipe,
)
from src.services.wiki_slug import read_frontmatter
from src.utils.config import Config

logger = logging.getLogger(__name__)


def _read_body(path: Path) -> str:
    """读 markdown 正文(剥离 frontmatter ``---`` 块)。"""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def _extract_links(body: str) -> list[str]:
    """从正文提取 ``[[目标]]`` / ``[[目标|显示]]`` / ``[[目标#锚点]]`` 的目标标题。"""
    out: list[str] = []
    for m in _WIKI_LINK_RE.finditer(body or ""):
        out.append(_strip_pipe(m.group(1).strip()))
    return out


class WikiFsLint:
    """扫描 ``wiki/<page_type>/*.md`` 产 LintReport(同 ``WikiLint.run()`` schema)。

    Args:
        wiki_dir: wiki 根目录。``None`` 时从 Config 读 ``knowledge_workflow.wiki_dir``
            (默认 ``wiki``)。测试注入 tmp 目录,生产走默认值。
    """

    def __init__(self, wiki_dir: str | Path | None = None) -> None:
        if wiki_dir is not None:
            self._wiki_dir = Path(wiki_dir)
        else:
            self._wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))

    def _collect_pages(self) -> list[dict]:
        pages: list[dict] = []
        if not self._wiki_dir.exists():
            return pages
        for ptype in PAGE_TYPE_DIRS:
            sub = self._wiki_dir / ptype
            if not sub.is_dir():
                continue
            for md in sorted(sub.glob("*.md")):
                fm = read_frontmatter(md)
                title = str(fm.get("title") or md.stem)
                pages.append({
                    "page_id": f"wiki:{ptype}:{md.stem}",
                    "page_type": ptype,
                    "path": md,
                    "title": title,
                    "frontmatter": fm,
                    "body": _read_body(md),
                })
        return pages

    def run(self) -> dict:
        pages = self._collect_pages()
        report = LintReport(total_pages=len(pages))
        if not pages:
            return report.to_dict()

        titles = {p["title"] for p in pages}
        # title -> 第一个匹配的 page_id(链接按标题解析,同名取首)
        title_to_pid: dict[str, str] = {}
        for p in pages:
            title_to_pid.setdefault(p["title"], p["page_id"])

        outbound: dict[str, set[str]] = {p["page_id"]: set() for p in pages}
        inbound: dict[str, set[str]] = {p["page_id"]: set() for p in pages}

        # 1. dead_reference + 构建链接图
        for p in pages:
            dead: list[str] = []
            seen: set[str] = set()
            for ref in _extract_links(p["body"]):
                if ref not in titles:
                    if ref not in seen:
                        seen.add(ref)
                        dead.append(ref)
                else:
                    tgt = title_to_pid[ref]
                    if tgt != p["page_id"]:  # 自环不计
                        outbound[p["page_id"]].add(tgt)
                        inbound[tgt].add(p["page_id"])
            if dead:
                report.findings.append(LintFinding(
                    severity="error", category="dead_reference",
                    page_id=p["page_id"], page_title=p["title"],
                    message=f"内容中有 {len(dead)} 个引用指向不存在的页面: {', '.join(dead[:5])}",
                    detail={"missing_titles": dead},
                ))

        # 2. orphan(无出链也无入链)+ missing_backlinks(无入链)
        for p in pages:
            has_out = bool(outbound[p["page_id"]])
            has_in = bool(inbound[p["page_id"]])
            if not has_out and not has_in:
                report.findings.append(LintFinding(
                    severity="warning", category="orphan",
                    page_id=p["page_id"], page_title=p["title"],
                    message="页面没有任何交叉引用链接",
                ))
            elif not has_in:
                report.findings.append(LintFinding(
                    severity="info", category="missing_backlinks",
                    page_id=p["page_id"], page_title=p["title"],
                    message="页面无入链(无其他 wiki 页引用)",
                    detail={},
                ))

        # 3. empty(正文为空)
        for p in pages:
            if not p["body"].strip():
                report.findings.append(LintFinding(
                    severity="info", category="empty",
                    page_id=p["page_id"], page_title=p["title"],
                    message="页面正文为空",
                ))

        # 4. duplicate(同名页面)
        title_counts: dict[str, list[str]] = defaultdict(list)
        for p in pages:
            title_counts[p["title"]].append(p["page_id"])
        for title, ids in title_counts.items():
            if len(ids) > 1:
                report.findings.append(LintFinding(
                    severity="warning", category="duplicate",
                    page_id=ids[0], page_title=title,
                    message=f"存在 {len(ids)} 个同名页面",
                    detail={"page_ids": ids},
                ))

        # 5. 溯源指标(DB 交叉校验)
        self._check_provenance(pages, report)

        # 汇总
        flagged = {f.page_id for f in report.findings}
        report.healthy_pages = sum(1 for p in pages if p["page_id"] not in flagged)
        report.score = report.healthy_pages / report.total_pages if report.total_pages else 1.0
        return report.to_dict()

    def _check_provenance(self, pages: list[dict], report: LintReport) -> None:
        """stale / outdated_claim:交叉校验 source 页的 knowledge_id/source_hash。

        仅对 sources 页(带 knowledge_id)生效。DB 不可用或无 knowledge_id 时跳过
        (不抛 — 与 wiki hook 同策略)。
        """
        try:
            from src.services.db import Database
        except Exception:  # pragma: no cover - db 不可用环境
            return
        for p in pages:
            if p["page_type"] != "sources":
                continue
            fm = p["frontmatter"]
            kid = fm.get("knowledge_id")
            if not kid:
                continue
            try:
                item = Database.get_knowledge(kid)
            except Exception:
                item = None
            if not item:
                report.findings.append(LintFinding(
                    severity="warning", category="stale",
                    page_id=p["page_id"], page_title=p["title"],
                    message=f"来源 knowledge {str(kid)[:8]} 已不存在",
                    detail={"knowledge_id": kid},
                ))
                continue
            page_hash = fm.get("source_hash", "")
            cur_hash = item.get("content_hash", "")
            if page_hash and cur_hash and page_hash != cur_hash:
                report.findings.append(LintFinding(
                    severity="warning", category="outdated_claim",
                    page_id=p["page_id"], page_title=p["title"],
                    message=f"源已变更(page hash {str(page_hash)[:8]} ≠ 当前 {str(cur_hash)[:8]})",
                    detail={"page_hash": page_hash, "current_hash": cur_hash},
                ))
