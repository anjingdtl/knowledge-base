"""ReadUseCase — application entry for document/block/claim/page reads (Phase 2).

Owns the typed-read dispatch that previously lived inline in MCP
``_resolve_read_target``. Returns a structured ``ReadResult`` so transport
adapters (MCP / REST / GUI) only need to wrap the payload in their envelope
shape; they must NOT call PassageStore / Database / WikiRepository private
methods directly (ADR retrieval-answer-boundaries-v2 §6).

The use case is deliberately vocabulary-free: it does not know about Golden
sets, evaluation cases, or any transport-specific error code. Transport
adapters translate a ``ReadResult`` with ``not_found=True`` into their own
``NOT_FOUND`` error envelope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReadRequest:
    knowledge_id: str | None = None
    block_id: str | None = None
    passage_id: str | None = None
    claim_id: str | None = None
    page_id: str | None = None
    item_id: str | None = None


@dataclass
class ReadResult:
    """Structured read outcome consumed by transport adapters.

    ``kind`` is one of: ``claim`` / ``block`` / ``wiki_page`` / ``knowledge`` /
    ``not_found`` / ``legacy_fallback``.

    * ``payload`` carries the typed object (already a plain dict, ready to
      envelope-wrap).
    * ``not_found`` is True when the target was typed-correctly but the
      backing store has no row. Adapters map this to ``ErrorCode.NOT_FOUND``.
    * ``error`` carries an exception message when the read raised; adapters
      map this to ``ErrorCode.INTERNAL_ERROR``.
    * ``legacy_fallback`` is True when no typed target was resolved — the
      adapter must fall through to the legacy knowledge-item read path.
    """

    kind: str
    payload: dict[str, Any] | None = None
    not_found: bool = False
    error: str | None = None
    legacy_fallback: bool = False
    target_id: str | None = None


class ReadUseCase:
    """Typed-read dispatch for claim / block / wiki_page / knowledge.

    The use case accepts a container exposing ``wiki_repository`` /
    ``wiki_serving_gate`` / ``db``. It never returns an MCP envelope — that
    is the adapter's job.
    """

    def __init__(self, knowledge_repository: Any = None, container: Any = None):
        self._repo = knowledge_repository
        self._container = container

    # ------------------------------------------------------------------ #
    # Public entry points                                                 #
    # ------------------------------------------------------------------ #

    def execute(self, request: ReadRequest) -> dict[str, Any]:
        """Legacy knowledge-item read by ``knowledge_id``.

        Kept for backward compatibility with callers that already use the
        ``ReadRequest{knowledge_id=...}`` shape. Typed reads (claim/block/
        page) go through :meth:`resolve_typed`.
        """
        kid = request.knowledge_id
        if not kid and self._container is not None and request.block_id:
            db = getattr(self._container, "db", None)
            if db is not None and hasattr(db, "get_block"):
                block = db.get_block(request.block_id) or {}
                kid = block.get("page_id") or block.get("knowledge_id")
        if self._repo is not None and kid and hasattr(self._repo, "get"):
            item = self._repo.get(kid)
            return {"knowledge_id": kid, "item": item}
        if self._container is not None and kid:
            db = getattr(self._container, "db", None)
            if db is not None and hasattr(db, "get_knowledge"):
                return {"knowledge_id": kid, "item": db.get_knowledge(kid)}
        return {"knowledge_id": kid, "item": None}

    def resolve_typed(self, request: ReadRequest) -> ReadResult:
        """Dispatch a typed read to the right backing store.

        Returns a :class:`ReadResult`. When no typed target can be resolved
        (e.g. bare ``item_id`` without a ``claim:`` / ``block:`` / ``page:``
        prefix that does not look like a claim), returns
        ``ReadResult(kind="legacy_fallback", legacy_fallback=True)`` so the
        adapter falls through to the legacy knowledge-item path.
        """
        raw = (
            request.claim_id
            or request.block_id
            or request.page_id
            or request.knowledge_id
            or request.item_id
            or ""
        ).strip()
        if not raw and not any([
            request.claim_id,
            request.block_id,
            request.page_id,
            request.knowledge_id,
        ]):
            return ReadResult(kind="legacy_fallback", legacy_fallback=True)

        kind: str | None = None
        value = raw
        if request.claim_id:
            kind, value = "claim", request.claim_id
        elif request.block_id:
            kind, value = "block", request.block_id
        elif request.page_id:
            kind, value = "page", request.page_id
        elif request.knowledge_id:
            kind, value = "knowledge", request.knowledge_id
        elif request.item_id:
            lower = request.item_id.lower()
            if lower.startswith("claim:"):
                kind, value = "claim", request.item_id.split(":", 1)[1]
            elif lower.startswith("block:"):
                kind, value = "block", request.item_id.split(":", 1)[1]
            elif lower.startswith("page:"):
                kind, value = "page", request.item_id.split(":", 1)[1]
            else:
                # Heuristic: claim_ prefix or looks like claim id in wiki repo.
                if request.item_id.startswith("claim_") or request.item_id.startswith("cl_"):
                    kind, value = "claim", request.item_id
                else:
                    return ReadResult(kind="legacy_fallback", legacy_fallback=True)

        if kind == "claim":
            return self._read_claim(value)
        if kind == "block":
            return self._read_block(value)
        if kind == "page":
            return self._read_page(value)
        if kind == "knowledge":
            return self._read_knowledge(value)
        return ReadResult(kind="legacy_fallback", legacy_fallback=True)

    # ------------------------------------------------------------------ #
    # Typed readers                                                       #
    # ------------------------------------------------------------------ #

    def _read_claim(self, claim_id: str) -> ReadResult:
        container = self._container
        if container is None:
            return ReadResult(
                kind="claim", target_id=claim_id,
                error="container_unavailable",
            )
        repo = getattr(container, "wiki_repository", None)
        try:
            claim = repo.get_claim(claim_id) if repo is not None else None
        except Exception as exc:  # noqa: BLE001
            return ReadResult(
                kind="claim", target_id=claim_id,
                error=f"读取 Claim 失败: {exc}",
            )
        if claim is None:
            return ReadResult(
                kind="claim", target_id=claim_id, not_found=True,
                error=f"Claim 不存在: {claim_id}",
            )
        gate = getattr(container, "wiki_serving_gate", None)
        decision = None
        if gate is not None:
            try:
                decision = gate.evaluate(claim)
            except Exception:  # noqa: BLE001
                decision = None
        evidence_rows = self._collect_claim_evidence(container, claim)
        relations = self._collect_claim_relations(claim)
        payload = {
            "type": "claim",
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "normalized_statement": claim.normalized_statement,
            "status": claim.status.value if hasattr(claim.status, "value") else str(claim.status),
            "revision": claim.revision,
            "confidence": claim.confidence,
            "relations": relations,
            "evidence": evidence_rows,
            "serving": {
                "eligible": bool(decision.eligible) if decision else None,
                "disclose_only": bool(decision.disclose_only) if decision else None,
                "reason_codes": list(decision.reason_codes) if decision else [],
            },
        }
        return ReadResult(kind="claim", target_id=claim_id, payload=payload)

    def _read_block(self, block_id: str) -> ReadResult:
        container = self._container
        if container is None:
            return ReadResult(
                kind="block", target_id=block_id,
                error="container_unavailable",
            )
        db = getattr(container, "db", None)
        if db is None:
            return ReadResult(
                kind="block", target_id=block_id,
                error="db_unavailable",
            )
        try:
            row = db.get_conn().execute(
                "SELECT id, page_id, content, block_type, properties, order_idx "
                "FROM blocks WHERE id = ?",
                (block_id,),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            return ReadResult(
                kind="block", target_id=block_id,
                error=f"读取 Block 失败: {exc}",
            )
        if row is None:
            return ReadResult(
                kind="block", target_id=block_id, not_found=True,
                error=f"Block 不存在: {block_id}",
            )
        if hasattr(row, "keys"):
            block = dict(row)
        else:
            block = {
                "id": row[0], "page_id": row[1], "content": row[2],
                "block_type": row[3], "properties": row[4], "order_idx": row[5],
            }
        kid = block.get("page_id") or ""
        item = db.get_knowledge(kid) if kid else None
        payload = {
            "type": "block",
            "block_id": block.get("id"),
            "knowledge_id": kid,
            "content": block.get("content"),
            "block_type": block.get("block_type"),
            "properties": block.get("properties"),
            "knowledge": item,
        }
        return ReadResult(kind="block", target_id=block_id, payload=payload)

    def _read_page(self, page_id: str) -> ReadResult:
        container = self._container
        if container is None:
            return ReadResult(
                kind="page", target_id=page_id,
                error="container_unavailable",
            )
        repo = getattr(container, "wiki_repository", None)
        try:
            page = (
                repo.get_page(page_id)
                if repo is not None and hasattr(repo, "get_page")
                else None
            )
        except Exception as exc:  # noqa: BLE001
            return ReadResult(
                kind="page", target_id=page_id,
                error=f"读取 Page 失败: {exc}",
            )
        if page is None:
            # Fall back to knowledge item so callers using wiki-style ids still
            # resolve. The adapter decides whether to treat this as wiki_page
            # or knowledge based on the returned ``type``.
            db = getattr(container, "db", None)
            item = db.get_knowledge(page_id) if db is not None else None
            if item:
                payload = {"type": "knowledge", **item}
                return ReadResult(kind="knowledge", target_id=page_id, payload=payload)
            return ReadResult(
                kind="page", target_id=page_id, not_found=True,
                error=f"Page 不存在: {page_id}",
            )
        if hasattr(page, "to_dict"):
            payload = page.to_dict()
        elif isinstance(page, dict):
            payload = page
        else:
            payload = {
                "page_id": getattr(page, "page_id", page_id),
                "title": getattr(page, "title", ""),
                "status": str(getattr(page, "status", "")),
            }
        payload = dict(payload)
        payload["type"] = "wiki_page"
        return ReadResult(kind="wiki_page", target_id=page_id, payload=payload)

    def _read_knowledge(self, knowledge_id: str) -> ReadResult:
        container = self._container
        if container is None:
            return ReadResult(
                kind="knowledge", target_id=knowledge_id,
                error="container_unavailable",
            )
        db = getattr(container, "db", None)
        if db is None:
            return ReadResult(
                kind="knowledge", target_id=knowledge_id,
                error="db_unavailable",
            )
        item = db.get_knowledge(knowledge_id)
        if not item:
            return ReadResult(
                kind="knowledge", target_id=knowledge_id, not_found=True,
                error=f"知识条目不存在: {knowledge_id}",
            )
        payload = {"type": "knowledge", **item}
        return ReadResult(kind="knowledge", target_id=knowledge_id, payload=payload)

    # ------------------------------------------------------------------ #
    # Claim evidence / relation collection                               #
    # ------------------------------------------------------------------ #

    def _collect_claim_evidence(self, container: Any, claim: Any) -> list[dict[str, Any]]:
        db = getattr(container, "db", None)
        rows: list[dict[str, Any]] = []
        for ev in claim.evidence:
            block = None
            try:
                if ev.block_id and db is not None:
                    block = db.get_conn().execute(
                        "SELECT id, page_id, content, properties FROM blocks WHERE id = ?",
                        (ev.block_id,),
                    ).fetchone()
            except Exception:  # noqa: BLE001
                block = None
            block_dict = (
                dict(block)
                if block is not None and hasattr(block, "keys")
                else (
                    {"id": block[0], "page_id": block[1], "content": block[2]}
                    if block is not None
                    else None
                )
            )
            rows.append({
                "evidence_id": ev.evidence_id,
                "knowledge_id": ev.knowledge_id,
                "block_id": ev.block_id,
                "stance": ev.stance.value if hasattr(ev.stance, "value") else str(ev.stance),
                "stale": bool(ev.stale),
                "excerpt_hash": ev.excerpt_hash,
                "excerpt": (block_dict or {}).get("content", "")[:500] if block_dict else "",
                "valid": (not ev.stale) and block_dict is not None,
            })
        return rows

    @staticmethod
    def _collect_claim_relations(claim: Any) -> list[dict[str, Any]]:
        relations: list[dict[str, Any]] = []
        for rel in (claim.relations or []):
            if hasattr(rel, "__dict__"):
                relations.append(
                    {
                        k: getattr(rel, k)
                        for k in ("relation_type", "target_id", "direction")
                        if hasattr(rel, k)
                    }
                    or {"raw": str(rel)}
                )
            elif isinstance(rel, dict):
                relations.append(rel)
            else:
                relations.append({"raw": str(rel)})
        return relations
