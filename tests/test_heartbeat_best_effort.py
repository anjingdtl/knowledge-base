"""Heartbeat is observability-only and must not break read tools (ADR §6)."""

from __future__ import annotations

from types import SimpleNamespace


def test_search_survives_heartbeat_beat_failure(monkeypatch):
    from src.mcp.tools import retrieval
    from src.services import mcp_heartbeat

    def boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(mcp_heartbeat, "beat", boom)
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(search_service=None, hybrid_search=None, db=None),
    )

    def empty_ft(query, limit=10, offset=0):
        return {"ok": True, "data": [], "meta": {"top_score": 0.0}}

    monkeypatch.setattr(retrieval, "search_fulltext", empty_ft)

    res = retrieval.search(query="不存在的查询xyz", limit=3)
    assert res["ok"] is True
    assert "data" in res
    # Beat failure must not surface as tool failure / TypeError envelope.


def test_ping_survives_heartbeat_beat_failure(monkeypatch):
    from src.mcp.tools import support
    from src.services import mcp_heartbeat

    # Locate ping if registered via support or retrieval/administration.
    ping = None
    for mod_name in (
        "src.mcp.tools.support",
        "src.mcp.tools.operations",
        "src.mcp.tools.administration",
        "src.mcp.server",
    ):
        try:
            mod = __import__(mod_name, fromlist=["ping"])
        except ImportError:
            continue
        if hasattr(mod, "ping"):
            ping = getattr(mod, "ping")
            break

    if ping is None:
        # Fall back: decorate a dummy and ensure decorator itself is best-effort.
        def boom():
            raise OSError("no data dir")

        monkeypatch.setattr(mcp_heartbeat, "beat", boom)

        @support.heartbeat
        def sample():
            return {"ok": True, "data": "pong"}

        assert sample() == {"ok": True, "data": "pong"}
        return

    def boom():
        raise OSError("no data dir")

    monkeypatch.setattr(mcp_heartbeat, "beat", boom)
    result = ping()
    # Envelope or plain dict — must not raise.
    assert result is not None
