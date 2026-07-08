"""WikiQueryService — 统一 Wiki 读取端口(Phase 3.5 / C4)。

唯一 wiki 查询入口,内部读取顺序(spec C4):
    Healthy V2 Projection (wiki_pages_v2 / wiki_pages_v2_fts)
        ↓ failure / disabled / empty
    Canonical Filesystem (wiki/*.md via WikiRepository)
        ↓ unavailable
    Legacy SQLite Compatibility Read (wiki_pages 旧表,可选)

职责:
- 统一候选 schema(WikiCandidate):page_id/title/status/claim_ids/source_ids/
  revision/match_source/warnings 一致
- 统一 fallback 顺序与 projection_drift warning
- 消费 wiki_pages_v2_fts(C0 审计:此前被写入但零查询)—— projection-first 搜索

显式依赖注入(repository/projection/database/config),不抓全局(C6)。
本端口在 Phase 4 主路径切换时逐步替换 SearchService/RagPipeline/WikiReadStage/
MCP/API 各自的 wiki 读取;C4 仅提供端口 + 契约一致性。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, cast

from src.models.wiki_v2 import Claim, WikiPage

logger = logging.getLogger(__name__)


@dataclass
class WikiCandidate:
    """统一 wiki 候选 schema(各入口返回一致)。"""

    page_id: str
    title: str
    page_type: str = ""
    status: str = ""
    claim_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    revision: int = 0
    content: str = ""
    score: float = 0.0
    match_source: str = ""  # "projection" | "filesystem" | "legacy_sqlite"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id, "title": self.title, "page_type": self.page_type,
            "status": self.status, "claim_ids": list(self.claim_ids),
            "source_ids": list(self.source_ids), "revision": self.revision,
            "content": self.content, "score": self.score,
            "match_source": self.match_source, "warnings": list(self.warnings),
        }


@dataclass
class WikiReadHealth:
    """wiki 读取健康状态。"""

    projection_enabled: bool = False
    projection_status: str = "unknown"  # healthy | stale | disabled | error
    page_count: int = 0
    claim_count: int = 0
    drift_count: int = 0
    warnings: list[str] = field(default_factory=list)


class WikiQueryService:
    """统一 wiki 读取端口。projection-first,FS fallback,legacy SQLite 兜底。"""

    def __init__(
        self,
        repository: Any,
        projection: Any,
        database: Any = None,
        config: Any = None,
    ) -> None:
        self._repo = repository
        self._proj = projection
        self._db = database
        self._config = config

    def _cfg(self, key: str, default: Any = None) -> Any:
        if self._config is not None:
            return self._config.get(key, default)
        return default

    # ------------------------------------------------------------------
    # 单对象读取
    # ------------------------------------------------------------------
    def get_page(self, page_id: str) -> WikiPage | None:
        """projection 优先 → FS fallback。legacy SQLite 旧表无 page_id 映射,不参与。"""
        if self._proj.enabled:
            try:
                page = self._page_from_projection(page_id)
                if page is not None:
                    return page
            except Exception:  # noqa: BLE001
                logger.warning("projection get_page failed, falling back to FS", exc_info=True)
        return cast("WikiPage | None", self._repo.get_page(page_id))

    def get_claim(self, claim_id: str) -> Claim | None:
        """Claim 只存于 Canonical FS(canonical store),直接读 repository。"""
        return cast("Claim | None", self._repo.get_claim(claim_id))

    def _page_from_projection(self, page_id: str) -> WikiPage | None:
        """从 wiki_pages_v2 读单页(projection)。"""
        conn = self._proj._db.get_conn()  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT page_id, path, title, page_type, status, revision, content, content_hash, "
            "aliases_json, tags_json, source_ids_json, claim_ids_json, created_at, updated_at "
            "FROM wiki_pages_v2 WHERE page_id = ?",
            (page_id,),
        ).fetchone()
        if row is None:
            return None
        return WikiPage(
            schema_version=2, page_id=row[0], title=row[2], page_type=row[3],  # type: ignore[arg-type]
            status=row[4], revision=row[5], content_hash=row[7], body=row[6],  # type: ignore[arg-type]
            aliases=json.loads(row[8] or "[]"), tags=json.loads(row[9] or "[]"),
            source_ids=json.loads(row[10] or "[]"), claim_ids=json.loads(row[11] or "[]"),
            created_at=row[12], updated_at=row[13],  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------
    # 搜索(消费 wiki_pages_v2_fts)
    # ------------------------------------------------------------------
    def search_pages(self, query: str, limit: int = 10) -> tuple[list[WikiCandidate], list[str]]:
        """统一搜索:projection fts 优先 → FS → legacy SQLite。

        Returns:
            (候选列表, warnings)。warnings 含 projection_drift / fallback 说明,
            各入口语义一致。
        """
        query = (query or "").strip()
        warnings: list[str] = []
        if not query:
            return [], []

        # 1. projection-first:wiki_pages_v2_fts MATCH(消费 fts,消除零读取)
        if self._proj.enabled:
            try:
                proj_candidates = self._fts_search(query, limit)
                if proj_candidates:
                    return proj_candidates, warnings
                # projection 命中 0 → 不立即 fallback,先看 parity(可能 projection 空)
                if self._projection_empty():
                    warnings.append("projection empty; falling back to filesystem")
                else:
                    return [], warnings  # projection 有数据但无命中 → 返回空(一致语义)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"projection fts failed: {exc}; falling back to filesystem")

        # 2. FS fallback
        fs_candidates = self._fs_search(query, limit)
        if not self._proj.enabled:
            warnings.append("projection disabled; using filesystem")
        for c in fs_candidates:
            c.match_source = "filesystem"
        if fs_candidates:
            return fs_candidates, warnings

        # 3. legacy SQLite fallback(可选)
        if self._db is not None and self._cfg("wiki.canonical_v2.compatibility_read_legacy_sqlite", True):
            legacy = self._legacy_sqlite_search(query, limit)
            if legacy:
                warnings.append("legacy sqlite fallback used")
                return legacy, warnings

        return [], warnings

    def _projection_empty(self) -> bool:
        try:
            conn = self._proj._db.get_conn()  # type: ignore[attr-defined]
            row = conn.execute("SELECT COUNT(*) FROM wiki_pages_v2").fetchone()
            return bool(row is None or row[0] == 0)
        except Exception:  # noqa: BLE001
            return True

    def _fts_search(self, query: str, limit: int) -> list[WikiCandidate]:
        """wiki_pages_v2_fts MATCH 搜索(消费 fts,消除 C0 审计的零读取)。"""
        conn = self._proj._db.get_conn()  # type: ignore[attr-defined]
        # fts5 MATCH:双引号包裹 query 做短语匹配,避免特殊字符破坏语法
        safe = query.replace('"', " ")
        rows = conn.execute(
            "SELECT p.page_id, p.title, p.page_type, p.status, p.revision, "
            "p.claim_ids_json, p.source_ids_json, p.content "
            "FROM wiki_pages_v2_fts f JOIN wiki_pages_v2 p ON p.page_id = f.page_id "
            "WHERE wiki_pages_v2_fts MATCH ? LIMIT ?",
            (f'"{safe}"', limit),
        ).fetchall()
        candidates: list[WikiCandidate] = []
        for r in rows:
            candidates.append(WikiCandidate(
                page_id=r[0], title=r[1], page_type=r[2], status=r[3], revision=r[4],
                claim_ids=json.loads(r[5] or "[]"), source_ids=json.loads(r[6] or "[]"),
                content=r[7] or "", match_source="projection",
            ))
        return candidates

    def _fs_search(self, query: str, limit: int) -> list[WikiCandidate]:
        """FS 搜索:扫 wiki/*.md 简单子串匹配(供 projection 不可用时 fallback)。"""
        q_lower = query.lower()
        candidates: list[WikiCandidate] = []
        for page in self._repo.list_pages():
            score = 0.0
            if q_lower in page.title.lower():
                score += 3.0
            if q_lower in page.body.lower():
                score += 1.0
            if score > 0:
                candidates.append(WikiCandidate(
                    page_id=page.page_id, title=page.title, page_type=page.page_type.value,
                    status=page.status.value, revision=page.revision,
                    claim_ids=list(page.claim_ids), source_ids=list(page.source_ids),
                    content=page.body, score=score,
                ))
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:limit]

    def _legacy_sqlite_search(self, query: str, limit: int) -> list[WikiCandidate]:
        """legacy SQLite wiki_pages 旧表兼容读(无 page_id,match_source 标 legacy)。"""
        try:
            rows = self._db.search_wiki_fts(query, limit=limit)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return []
        candidates: list[WikiCandidate] = []
        for r in rows or []:
            candidates.append(WikiCandidate(
                page_id=str(r.get("id", "")), title=str(r.get("title", "")),
                status=str(r.get("status", "")), content=str(r.get("content", "")),
                match_source="legacy_sqlite",
            ))
        return candidates

    # ------------------------------------------------------------------
    # 健康状态
    # ------------------------------------------------------------------
    def health(self) -> WikiReadHealth:
        """projection parity / drift,统一暴露给各入口。"""
        h = WikiReadHealth(projection_enabled=bool(self._proj.enabled))
        try:
            page_count = len(self._repo.list_pages())
            claim_count = len(self._repo.list_claims())
            h.page_count = page_count
            h.claim_count = claim_count
        except Exception:  # noqa: BLE001
            h.warnings.append("repository list failed")
        if not self._proj.enabled:
            h.projection_status = "disabled"
            return h
        try:
            findings = self._proj.verify_parity()  # type: ignore[attr-defined]
            h.drift_count = len(findings)
            h.projection_status = "healthy" if not findings else "stale"
            for f in findings:
                h.warnings.append(f"projection_drift: {f.message}")
        except Exception as exc:  # noqa: BLE001
            h.projection_status = "error"
            h.warnings.append(f"projection parity check failed: {exc}")
        return h
