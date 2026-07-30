"""ReadUseCase + MCP ``_resolve_read_target`` boundary tests (Phase 2 Task 2.4).

Verifies that the MCP ``read`` adapter delegates typed-read dispatch to
:class:`ReadUseCase.resolve_typed` rather than calling DB SQL / wiki repo /
serving gate inline. Tests inject fakes via the container and assert the
use case is invoked with the right ``ReadRequest``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class _FakeRow:
    """sqlite Row-like object exposing both index and key access."""

    def __init__(self, mapping: dict[str, Any]):
        self._m = mapping

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._m.values())[key]
        return self._m[key]

    def keys(self):
        return self._m.keys()

    def __iter__(self):
        return iter(self._m.values())


class _FakeConn:
    """Fake DB connection that returns rows keyed by the first SQL param.

    ``rows`` maps block_id (the first bound param) → the matching row, or
    ``None`` when no row should match. Multiple SQL statements can share the
    same fake conn — the param value alone decides which row is returned.
    """

    def __init__(self, rows: dict[str, Any] | None = None):
        self._rows = rows or {}
        self.calls: list[str] = []

    def execute(self, sql: str, params):
        self.calls.append(sql)
        target = params[0] if params else ""
        row = self._rows.get(target)
        return SimpleNamespace(fetchone=lambda r=row: r)

    def fetchone(self):
        return None


class _FakeDB:
    def __init__(self, knowledge_items: dict[str, dict] | None = None,
                 conn: _FakeConn | None = None):
        self._knowledge = knowledge_items or {}
        self._conn = conn

    def get_conn(self):
        return self._conn

    def get_knowledge(self, kid):
        return self._knowledge.get(kid)


class _FakeWikiRepo:
    def __init__(self, claims: dict[str, Any] | None = None,
                 pages: dict[str, Any] | None = None):
        self._claims = claims or {}
        self._pages = pages or {}

    def get_claim(self, claim_id):
        return self._claims.get(claim_id)

    def get_page(self, page_id):
        return self._pages.get(page_id)


class _FakeStance:
    def __init__(self, value: str = "supports"):
        self.value = value


class _FakeEvidence:
    def __init__(self, *, evidence_id, knowledge_id, block_id,
                 stance=_FakeStance(), stale=False, excerpt_hash="h"):
        self.evidence_id = evidence_id
        self.knowledge_id = knowledge_id
        self.block_id = block_id
        self.stance = stance
        self.stale = stale
        self.excerpt_hash = excerpt_hash


class _FakeClaim:
    def __init__(self, claim_id, statement="s", evidence=None, relations=None):
        self.claim_id = claim_id
        self.statement = statement
        self.normalized_statement = statement
        self.status = SimpleNamespace(value="approved")
        self.revision = 1
        self.confidence = 0.9
        self.evidence = evidence or []
        self.relations = relations or []


class _FakeServingDecision:
    def __init__(self, eligible=True, disclose_only=False, reason_codes=None):
        self.eligible = eligible
        self.disclose_only = disclose_only
        self.reason_codes = reason_codes or []


class _FakeServingGate:
    def __init__(self, decision=None):
        self._decision = decision
        self.calls: list[Any] = []

    def evaluate(self, claim):
        self.calls.append(claim)
        return self._decision


def _install_read_use_case(monkeypatch, container, *, read_use_case=None):
    from src.mcp.tools import retrieval

    if read_use_case is not None:
        container.read_use_case = read_use_case
    monkeypatch.setattr(retrieval, "_get_container", lambda: container)
    return retrieval


# --------------------------------------------------------------------------- #
# ReadUseCase — direct unit tests (no MCP envelope)                           #
# --------------------------------------------------------------------------- #


def test_read_use_case_resolves_claim_with_evidence_and_relations():
    block_row = _FakeRow({
        "id": "b1", "page_id": "k1", "content": "block content",
        "properties": "{}",
    })
    conn = _FakeConn(rows={"b1": block_row})
    db = _FakeDB(knowledge_items={"k1": {"id": "k1", "title": "t"}}, conn=conn)
    claim = _FakeClaim(
        "c1", statement="s",
        evidence=[_FakeEvidence(evidence_id="e1", knowledge_id="k1", block_id="b1")],
        relations=[SimpleNamespace(relation_type="relates", target_id="c2", direction="out")],
    )
    repo = _FakeWikiRepo(claims={"c1": claim})
    container = SimpleNamespace(db=db, wiki_repository=repo, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(claim_id="c1"))

    assert result.kind == "claim"
    assert result.target_id == "c1"
    assert result.payload["type"] == "claim"
    assert result.payload["claim_id"] == "c1"
    assert result.payload["evidence"][0]["block_id"] == "b1"
    assert result.payload["evidence"][0]["valid"] is True
    assert result.payload["relations"][0]["relation_type"] == "relates"


def test_read_use_case_resolves_block_with_knowledge():
    block_row = _FakeRow({
        "id": "b1", "page_id": "k1", "content": "block content",
        "block_type": "text", "properties": "{}", "order_idx": 0,
    })
    conn = _FakeConn(rows={"b1": block_row})
    db = _FakeDB(knowledge_items={"k1": {"id": "k1", "title": "t"}}, conn=conn)
    container = SimpleNamespace(db=db, wiki_repository=None, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(block_id="b1"))

    assert result.kind == "block"
    assert result.target_id == "b1"
    assert result.payload["block_id"] == "b1"
    assert result.payload["knowledge_id"] == "k1"
    assert result.payload["knowledge"]["title"] == "t"


def test_read_use_case_block_not_found():
    conn = _FakeConn({})
    db = _FakeDB(conn=conn)
    container = SimpleNamespace(db=db, wiki_repository=None, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(block_id="missing"))

    assert result.kind == "block"
    assert result.not_found is True
    assert result.payload is None
    assert "Block 不存在" in result.error


def test_read_use_case_resolves_wiki_page():
    page = SimpleNamespace(
        page_id="p1", title="t", status="published",
        to_dict=lambda: {"page_id": "p1", "title": "t", "status": "published"},
    )
    repo = _FakeWikiRepo(pages={"p1": page})
    container = SimpleNamespace(db=None, wiki_repository=repo, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(page_id="p1"))

    assert result.kind == "wiki_page"
    assert result.target_id == "p1"
    assert result.payload["type"] == "wiki_page"
    assert result.payload["page_id"] == "p1"


def test_read_use_case_page_falls_back_to_knowledge_when_no_wiki():
    db = _FakeDB(knowledge_items={"k1": {"id": "k1", "title": "t"}})
    container = SimpleNamespace(db=db, wiki_repository=None, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(page_id="k1"))

    assert result.kind == "knowledge"
    assert result.target_id == "k1"
    assert result.payload["type"] == "knowledge"


def test_read_use_case_resolves_knowledge():
    db = _FakeDB(knowledge_items={"k1": {"id": "k1", "title": "t"}})
    container = SimpleNamespace(db=db, wiki_repository=None, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(knowledge_id="k1"))

    assert result.kind == "knowledge"
    assert result.payload["type"] == "knowledge"


def test_read_use_case_knowledge_not_found():
    db = _FakeDB(knowledge_items={})
    container = SimpleNamespace(db=db, wiki_repository=None, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(knowledge_id="missing"))

    assert result.kind == "knowledge"
    assert result.not_found is True


def test_read_use_case_item_id_prefix_claim():
    """item_id with claim: prefix routes to claim reader."""
    claim = _FakeClaim("c1", statement="s")
    repo = _FakeWikiRepo(claims={"c1": claim})
    container = SimpleNamespace(db=None, wiki_repository=repo, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(item_id="claim:c1"))

    assert result.kind == "claim"
    assert result.target_id == "c1"


def test_read_use_case_item_id_prefix_block():
    """item_id with block: prefix routes to block reader."""
    block_row = _FakeRow({
        "id": "b1", "page_id": "k1", "content": "c", "block_type": "text",
        "properties": "{}", "order_idx": 0,
    })
    conn = _FakeConn(rows={"b1": block_row})
    db = _FakeDB(conn=conn)
    container = SimpleNamespace(db=db, wiki_repository=None, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(item_id="block:b1"))

    assert result.kind == "block"
    assert result.target_id == "b1"


def test_read_use_case_item_id_bare_returns_legacy_fallback():
    """item_id without prefix and without claim_ heuristic → legacy fallback."""
    container = SimpleNamespace(db=None, wiki_repository=None, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(item_id="plain-id"))

    assert result.kind == "legacy_fallback"
    assert result.legacy_fallback is True


def test_read_use_case_empty_request_returns_legacy_fallback():
    container = SimpleNamespace(db=None, wiki_repository=None, wiki_serving_gate=None)

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest())

    assert result.kind == "legacy_fallback"
    assert result.legacy_fallback is True


def test_read_use_case_claim_invokes_serving_gate():
    claim = _FakeClaim("c1")
    repo = _FakeWikiRepo(claims={"c1": claim})
    gate = _FakeServingGate(_FakeServingDecision(eligible=True))
    container = SimpleNamespace(
        db=_FakeDB(conn=_FakeConn({})),
        wiki_repository=repo,
        wiki_serving_gate=gate,
    )

    from src.application.read_use_case import ReadRequest, ReadUseCase

    use_case = ReadUseCase(container=container)
    result = use_case.resolve_typed(ReadRequest(claim_id="c1"))

    assert result.kind == "claim"
    assert len(gate.calls) == 1
    assert gate.calls[0] is claim
    assert result.payload["serving"]["eligible"] is True


# --------------------------------------------------------------------------- #
# MCP adapter — boundary delegation                                            #
# --------------------------------------------------------------------------- #


def test_mcp_resolve_read_target_prefers_container_read_use_case(monkeypatch):
    """When ``container.read_use_case`` is wired, MCP must delegate to it."""
    from src.application.read_use_case import ReadResult

    class _Recording:
        def __init__(self):
            self.calls: list[Any] = []

        def resolve_typed(self, request):
            self.calls.append(request)
            return ReadResult(
                kind="knowledge",
                target_id="k1",
                payload={"type": "knowledge", "id": "k1", "title": "t"},
            )

    container = MagicMock()
    fake = _Recording()
    retrieval = _install_read_use_case(monkeypatch, container, read_use_case=fake)

    out = retrieval._resolve_read_target(knowledge_id="k1")

    assert len(fake.calls) == 1
    assert fake.calls[0].knowledge_id == "k1"
    assert out is not None
    assert out["ok"] is True
    assert out["data"]["type"] == "knowledge"
    # knowledge_id kwarg lands in meta (per ok() envelope shape).
    assert out["meta"]["knowledge_id"] == "k1"


def test_mcp_resolve_read_target_returns_none_for_legacy_fallback(monkeypatch):
    """legacy_fallback result → MCP returns None (caller falls through)."""
    from src.application.read_use_case import ReadResult

    class _FallbackUseCase:
        def resolve_typed(self, request):
            return ReadResult(kind="legacy_fallback", legacy_fallback=True)

    container = MagicMock()
    retrieval = _install_read_use_case(monkeypatch, container, read_use_case=_FallbackUseCase())

    out = retrieval._resolve_read_target(item_id="plain-id")
    assert out is None


def test_mcp_resolve_read_target_not_found_returns_fail_envelope(monkeypatch):
    """not_found result → MCP returns a fail envelope with NOT_FOUND."""
    from src.application.read_use_case import ReadResult

    class _NotFoundUseCase:
        def resolve_typed(self, request):
            return ReadResult(
                kind="claim", target_id="c1", not_found=True,
                error="Claim 不存在: c1",
            )

    container = MagicMock()
    retrieval = _install_read_use_case(monkeypatch, container, read_use_case=_NotFoundUseCase())

    out = retrieval._resolve_read_target(claim_id="c1")
    assert out is not None
    assert out["ok"] is False
    assert out["error"]["code"] == "NOT_FOUND"
    # claim_id lands in error.details (per fail() envelope shape).
    assert out["error"]["details"]["claim_id"] == "c1"


def test_mcp_resolve_read_target_error_returns_fail_envelope(monkeypatch):
    """error result → MCP returns a fail envelope with INTERNAL_ERROR."""
    from src.application.read_use_case import ReadResult

    class _ErrorUseCase:
        def resolve_typed(self, request):
            return ReadResult(
                kind="block", target_id="b1",
                error="读取 Block 失败: db locked",
            )

    container = MagicMock()
    retrieval = _install_read_use_case(monkeypatch, container, read_use_case=_ErrorUseCase())

    out = retrieval._resolve_read_target(block_id="b1")
    assert out is not None
    assert out["ok"] is False
    assert out["error"]["code"] == "INTERNAL_ERROR"
    assert out["error"]["details"]["block_id"] == "b1"


def test_mcp_resolve_read_target_page_kwarg_preserved_for_wiki_page(monkeypatch):
    """page_id input → envelope keeps page_id kwarg even when payload type is wiki_page."""
    from src.application.read_use_case import ReadResult

    class _WikiPageUseCase:
        def resolve_typed(self, request):
            return ReadResult(
                kind="wiki_page", target_id="p1",
                payload={"type": "wiki_page", "page_id": "p1", "title": "t"},
            )

    container = MagicMock()
    retrieval = _install_read_use_case(monkeypatch, container, read_use_case=_WikiPageUseCase())

    out = retrieval._resolve_read_target(page_id="p1")
    assert out is not None
    assert out["ok"] is True
    # page_id lands in meta (per ok() envelope shape).
    assert out["meta"]["page_id"] == "p1"


def test_mcp_resolve_read_target_fallback_constructs_use_case(monkeypatch):
    """When container lacks read_use_case, MCP constructs ReadUseCase(container)."""
    from types import SimpleNamespace

    container = SimpleNamespace(
        db=_FakeDB(knowledge_items={"k1": {"id": "k1", "title": "t"}}),
        wiki_repository=None,
        wiki_serving_gate=None,
    )
    retrieval = _install_read_use_case(monkeypatch, container)

    out = retrieval._resolve_read_target(knowledge_id="k1")
    assert out is not None
    assert out["ok"] is True
    assert out["data"]["type"] == "knowledge"


def test_mcp_resolve_read_target_get_use_case_is_idempotent_per_container(monkeypatch):
    """The resolver returns the same container-provided service on repeated calls."""
    from src.application.read_use_case import ReadResult

    class _Stub:
        def resolve_typed(self, request):
            return ReadResult(kind="legacy_fallback", legacy_fallback=True)

    container = MagicMock()
    stub = _Stub()
    retrieval = _install_read_use_case(monkeypatch, container, read_use_case=stub)

    svc1 = retrieval._get_read_use_case()
    svc2 = retrieval._get_read_use_case()
    assert svc1 is svc2 is stub


def test_mcp_retrieval_does_not_call_db_sql_directly():
    """ADR §6: MCP retrieval.py must not contain raw DB SQL for typed reads."""
    import inspect

    from src.mcp.tools import retrieval

    src_text = inspect.getsource(retrieval)
    # The block/claim SQL that used to live inline must now be gone.
    assert "SELECT id, page_id, content, block_type" not in src_text
    assert "SELECT id, page_id, content, properties FROM blocks" not in src_text
