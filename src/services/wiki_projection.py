"""Canonical Wiki v2 SQLite Projection Service.

消费 WikiRepository 写出的 outbox 事件，把 canonical page/claim/evidence
投影到 SQLite v2 表 + FTS，支持幂等消费、全量 rebuild、parity 校验。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.models.wiki_v2 import Claim, ValidationFinding, WikiPage

logger = logging.getLogger(__name__)


@dataclass
class ProjectionResult:
    processed: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class WikiProjection:
    """将 canonical filesystem 状态投影到 SQLite v2 表。

    幂等语义: process_outbox 重放全部 outbox 事件,INSERT OR REPLACE by PK;
    rebuild 清空后全量灌入;verify_parity 对比 FS 与投影。
    """

    def __init__(self, repository, database, enabled: bool = True) -> None:
        self._repo = repository
        self._db = database
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """canonical_v2 开关状态,locator enrichment 据此跳过无用的 DB 查询。"""
        return self._enabled

    # ---- 公共接口 ----

    def process_outbox(self, *, force: bool = False) -> ProjectionResult:
        """读 repository.read_outbox() 全部事件,按 type 投影。

        enabled=False -> 全跳过。逐事件 try/except: 单事件失败记 errors 继续下一个。
        幂等: 重放全 outbox, upsert 语义, 二次处理同 revision 为 no-op。
        """
        result = ProjectionResult()
        events = self._repo.read_outbox()
        if not self._enabled and not force:
            result.skipped = len(events)
            return result
        for event in events:
            etype = event.get("type", "")
            try:
                if etype in ("page.created", "page.updated"):
                    page_id = event["page_id"]
                    page = self._repo.get_page(page_id)
                    if page is None:
                        result.skipped += 1
                        result.warnings.append(
                            f"outbox {etype}: page {page_id} not found in FS"
                        )
                        continue
                    path = event.get("path")
                    if not path:
                        reg = self._repo.get_registry().get(page_id, {})
                        path = reg.get("path", "")
                    self._upsert_page(page, path)
                    result.processed += 1
                elif etype in ("claim.created", "claim.updated"):
                    claim_id = event["claim_id"]
                    claim = self._repo.get_claim(claim_id)
                    if claim is None:
                        result.skipped += 1
                        result.warnings.append(
                            f"outbox {etype}: claim {claim_id} not found in FS"
                        )
                        continue
                    self._upsert_claim(claim)
                    result.processed += 1
                elif etype == "claim.deleted":
                    self._delete_claim(event["claim_id"])
                    result.processed += 1
                else:
                    result.warnings.append(f"unknown outbox event type: {etype}")
            except Exception as exc:
                result.errors.append(f"{etype} error: {exc}")
        return result

    def rebuild(self) -> ProjectionResult:
        """全量重建: 清空 6 v2 表 + FTS, 再从 repository 全灌。

        原子性: 整个 rebuild 在单个事务内执行,中途失败会回滚到重建前状态。
        不受 enabled 影响(手动全量重建总是执行)。
        """
        result = ProjectionResult()
        conn = self._db.get_conn()
        try:
            self._clear_v2_tables(commit=False)
            # 投影所有 page
            pages = self._repo.list_pages()
            registry = self._repo.get_registry()
            for page in pages:
                entry = registry.get(page.page_id, {})
                path = entry.get("path", "")
                self._upsert_page(page, path, commit=False)
                result.processed += 1
            # 投影所有 claim
            claims = self._repo.list_claims()
            for claim in claims:
                self._upsert_claim(claim, commit=False)
                result.processed += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return result

    def verify_parity(self) -> list[ValidationFinding]:
        """对比 canonical FS vs projection,任何缺失或不符 -> ValidationFinding。

        返回空列表 = parity 完美。
        """
        findings: list[ValidationFinding] = []
        conn = self._db.get_conn()
        # 校验 pages
        pages = self._repo.list_pages()
        registry = self._repo.get_registry()
        for page in pages:
            row = conn.execute(
                "SELECT revision, content_hash FROM wiki_pages_v2 WHERE page_id = ?",
                (page.page_id,),
            ).fetchone()
            if row is None:
                findings.append(ValidationFinding(
                    path=registry.get(page.page_id, {}).get("path", ""),
                    object_id=page.page_id,
                    category="projection_drift",
                    severity="error",
                    message=f"page {page.page_id} missing from projection",
                ))
            elif row[0] != page.revision or row[1] != page.content_hash:
                findings.append(ValidationFinding(
                    path=registry.get(page.page_id, {}).get("path", ""),
                    object_id=page.page_id,
                    category="projection_drift",
                    severity="error",
                    message=f"page {page.page_id} drift: "
                            f"FS rev={page.revision} hash={page.content_hash}, "
                            f"DB rev={row[0]} hash={row[1]}",
                ))
        # 校验 claims
        claims = self._repo.list_claims()
        for claim in claims:
            row = conn.execute(
                "SELECT revision FROM wiki_claims WHERE claim_id = ?",
                (claim.claim_id,),
            ).fetchone()
            if row is None:
                findings.append(ValidationFinding(
                    path=f"claims/{claim.claim_id}.yaml",
                    object_id=claim.claim_id,
                    category="projection_drift",
                    severity="error",
                    message=f"claim {claim.claim_id} missing from projection",
                ))
            elif row[0] != claim.revision:
                findings.append(ValidationFinding(
                    path=f"claims/{claim.claim_id}.yaml",
                    object_id=claim.claim_id,
                    category="projection_drift",
                    severity="error",
                    message=f"claim {claim.claim_id} drift: "
                            f"FS rev={claim.revision}, DB rev={row[0]}",
                ))
        return findings

    # ---- 私有投影方法 ----

    def _upsert_page(self, page: WikiPage, path: str, *, commit: bool = True) -> None:
        """INSERT OR REPLACE wiki_pages_v2 (by page_id) + FTS + wiki_page_claims。"""
        conn = self._db.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO wiki_pages_v2 "
            "(page_id, path, title, page_type, status, revision, content, content_hash, "
            "aliases_json, tags_json, source_ids_json, claim_ids_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                page.page_id, path, page.title, page.page_type.value, page.status.value,
                page.revision, page.body, page.content_hash,
                json.dumps(page.aliases, ensure_ascii=False),
                json.dumps(page.tags, ensure_ascii=False),
                json.dumps(page.source_ids, ensure_ascii=False),
                json.dumps(page.claim_ids, ensure_ascii=False),
                page.created_at, page.updated_at,
            ),
        )
        # FTS: 先删后插 (幂等)
        conn.execute("DELETE FROM wiki_pages_v2_fts WHERE page_id = ?", (page.page_id,))
        conn.execute(
            "INSERT INTO wiki_pages_v2_fts (page_id, title, content) VALUES (?,?,?)",
            (page.page_id, page.title, page.body),
        )
        # wiki_page_claims: 先删后插 (幂等)
        conn.execute("DELETE FROM wiki_page_claims WHERE page_id = ?", (page.page_id,))
        for idx, cid in enumerate(page.claim_ids):
            conn.execute(
                "INSERT INTO wiki_page_claims (page_id, claim_id, display_order) VALUES (?,?,?)",
                (page.page_id, cid, idx),
            )
        # Phase 5:claim→page 边(read model)。先删该 page 的 cited_in 边再插(幂等)。
        conn.execute(
            "DELETE FROM wiki_dependencies WHERE to_type='page' AND to_id = ?",
            (page.page_id,),
        )
        if page.claim_ids:
            conn.executemany(
                "INSERT OR IGNORE INTO wiki_dependencies(from_type, from_id, to_type, to_id, relation) "
                "VALUES ('claim', ?, 'page', ?, 'cited_in')",
                [(cid, page.page_id) for cid in page.claim_ids],
            )
        self._upsert_legacy_page(page, commit=False)
        if commit:
            conn.commit()

    def _upsert_legacy_page(self, page: WikiPage, *, commit: bool = True) -> None:
        """Maintain the legacy wiki_pages read model from canonical pages."""
        conn = self._db.get_conn()
        if not self._legacy_page_table_exists(conn):
            return
        existing = conn.execute(
            "SELECT concept_summary, lint_score, complex_anomaly FROM wiki_pages WHERE id = ?",
            (page.page_id,),
        ).fetchone()
        conn.execute(
            "INSERT OR REPLACE INTO wiki_pages "
            "(id, title, content, source_ids, tags, concept_summary, status, "
            "lint_score, complex_anomaly, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                page.page_id,
                page.title,
                page.body,
                json.dumps(page.source_ids, ensure_ascii=False),
                json.dumps(page.tags, ensure_ascii=False),
                existing["concept_summary"] if existing else "",
                page.status.value,
                existing["lint_score"] if existing else 1.0,
                existing["complex_anomaly"] if existing else "",
                page.created_at,
                page.updated_at,
            ),
        )
        if commit:
            conn.commit()

    @staticmethod
    def _legacy_page_table_exists(conn) -> bool:
        """Return whether this projection database exposes the legacy read model."""
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'wiki_pages'"
        ).fetchone()
        return row is not None

    def update_legacy_page_fields(self, page_id: str, **fields) -> None:
        """Patch compatibility-only legacy wiki_pages fields."""
        if not fields:
            return
        allowed = {"content", "concept_summary", "lint_score", "complex_anomaly"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid legacy projection fields: {invalid}")
        conn = self._db.get_conn()
        if not self._legacy_page_table_exists(conn):
            return
        sets = ", ".join(f"{field} = ?" for field in fields)
        conn.execute(
            f"UPDATE wiki_pages SET {sets} WHERE id = ?",
            [*fields.values(), page_id],
        )
        conn.commit()

    def _upsert_claim(self, claim: Claim, *, commit: bool = True) -> None:
        """INSERT OR REPLACE wiki_claims (by claim_id, claim_scope=NULL) + evidence。"""
        conn = self._db.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO wiki_claims "
            "(claim_id, statement, normalized_statement, claim_type, status, confidence, "
            "claim_scope, valid_from, valid_to, revision, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                claim.claim_id, claim.statement, claim.normalized_statement,
                claim.claim_type, claim.status.value, claim.confidence,
                None, claim.valid_from, claim.valid_to,
                claim.revision, claim.created_at, claim.updated_at,
            ),
        )
        # evidence: 先删后插 (幂等)
        conn.execute("DELETE FROM wiki_claim_evidence WHERE claim_id = ?", (claim.claim_id,))
        for ev in claim.evidence:
            conn.execute(
                "INSERT INTO wiki_claim_evidence "
                "(evidence_id, claim_id, stance, knowledge_id, block_id, "
                "location_json, source_revision, excerpt_hash, observed_at, stale, stale_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ev.evidence_id, claim.claim_id, ev.stance.value,
                    ev.knowledge_id, ev.block_id,
                    json.dumps(ev.location, ensure_ascii=False),
                    ev.source_revision, ev.excerpt_hash, ev.observed_at,
                    1 if ev.stale else 0, ev.stale_at,
                ),
            )
        # Phase 5:依赖图边(read model)。
        # 仅清该 claim 作为 from/to 的非 cited_in 边，再重建 evidence/relation 边；
        # cited_in 由 page 投影写入，claim-only 更新不得抹掉。
        conn.execute(
            "DELETE FROM wiki_dependencies WHERE "
            "(from_id = ? OR to_id = ?) AND relation != 'cited_in'",
            (claim.claim_id, claim.claim_id),
        )
        edge_rows: list[tuple] = []
        for ev in claim.evidence:
            edge_rows.append(("source", ev.knowledge_id, "evidence", ev.evidence_id, "produces"))
            edge_rows.append(("evidence", ev.evidence_id, "claim", claim.claim_id, "evidences"))
        for rel in claim.relations:
            edge_rows.append(("claim", claim.claim_id, "claim", rel.target_claim_id, rel.relation))
        # 若投影表 wiki_page_claims 仍引用该 claim，回补 cited_in
        page_rows = conn.execute(
            "SELECT page_id FROM wiki_page_claims WHERE claim_id = ?",
            (claim.claim_id,),
        ).fetchall()
        for (page_id,) in page_rows:
            edge_rows.append(("claim", claim.claim_id, "page", page_id, "cited_in"))
        if edge_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO wiki_dependencies(from_type, from_id, to_type, to_id, relation) "
                "VALUES (?,?,?,?,?)",
                edge_rows,
            )
        if commit:
            conn.commit()

    def _delete_page(self, page_id: str, *, commit: bool = True) -> None:
        """DELETE FROM wiki_pages_v2 / FTS / wiki_page_claims。"""
        conn = self._db.get_conn()
        conn.execute("DELETE FROM wiki_page_claims WHERE page_id = ?", (page_id,))
        conn.execute("DELETE FROM wiki_pages_v2_fts WHERE page_id = ?", (page_id,))
        conn.execute("DELETE FROM wiki_pages_v2 WHERE page_id = ?", (page_id,))
        if commit:
            conn.commit()

    def _delete_claim(self, claim_id: str, *, commit: bool = True) -> None:
        """DELETE FROM wiki_claims / wiki_claim_evidence / 依赖边 / page_claims。"""
        conn = self._db.get_conn()
        conn.execute("DELETE FROM wiki_claim_evidence WHERE claim_id = ?", (claim_id,))
        conn.execute("DELETE FROM wiki_claims WHERE claim_id = ?", (claim_id,))
        conn.execute("DELETE FROM wiki_page_claims WHERE claim_id = ?", (claim_id,))
        conn.execute(
            "DELETE FROM wiki_dependencies WHERE from_id = ? OR to_id = ?",
            (claim_id, claim_id),
        )
        if commit:
            conn.commit()

    def find_page_id_by_path(self, path: str) -> str | None:
        """按 canonical 相对路径查 wiki_pages_v2 的 page_id。表空/无行/异常 -> None。"""
        try:
            conn = self._db.get_conn()
            row = conn.execute(
                "SELECT page_id FROM wiki_pages_v2 WHERE path = ?", (path,)
            ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def _clear_v2_tables(self, *, commit: bool = True) -> None:
        """DELETE FROM 全部 v2 表 (rebuild 用), wiki_projection_state 保留。"""
        conn = self._db.get_conn()
        tables = [
            "wiki_page_claims",
            "wiki_claim_evidence",
            "wiki_dependencies",
            "wiki_pages_v2_fts",
            "wiki_claims",
            "wiki_pages_v2",
        ]
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        if commit:
            conn.commit()
