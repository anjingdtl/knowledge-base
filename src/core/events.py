"""
ShineHeKnowledge Event Bus — blinker-based pub-sub for domain events.

Usage:
    from src.core.events import on, emit

    on("knowledge.created", my_handler)   # subscribe
    emit("knowledge.created", id=42)      # publish
"""

from __future__ import annotations

from blinker import Namespace

# ---------------------------------------------------------------------------
# Signal registry
# ---------------------------------------------------------------------------
_ns = Namespace()

# Knowledge entries
knowledge_created = _ns.signal("knowledge.created")
knowledge_updated = _ns.signal("knowledge.updated")
knowledge_deleted = _ns.signal("knowledge.deleted")

# Chunks
chunk_indexed = _ns.signal("chunk.indexed")
chunk_deleted = _ns.signal("chunk.deleted")

# Wiki
wiki_page_created = _ns.signal("wiki.page_created")
wiki_page_updated = _ns.signal("wiki.page_updated")
wiki_status_changed = _ns.signal("wiki.status_changed")

# Knowledge graph
graph_updated = _ns.signal("graph.updated")

# Async jobs
job_created = _ns.signal("job.created")
job_completed = _ns.signal("job.completed")
job_failed = _ns.signal("job.failed")

# Embedding cache
embedding_cached = _ns.signal("embedding.cached")

# LLM status
llm_status_changed = _ns.signal("llm.status_changed")

# ---------------------------------------------------------------------------
# Name -> signal lookup (for string-based API)
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, "_ns.Signal"] = {  # type: ignore[name-defined]
    s.name: s for s in [
        knowledge_created, knowledge_updated, knowledge_deleted,
        chunk_indexed, chunk_deleted,
        wiki_page_created, wiki_page_updated, wiki_status_changed,
        graph_updated,
        job_created, job_completed, job_failed,
        embedding_cached,
        llm_status_changed,
    ]
}


def _resolve(name: str):
    """Resolve dotted signal name to blinker Signal."""
    sig = _REGISTRY.get(name)
    if sig is None:
        raise KeyError(f"Unknown event signal: {name!r}")
    return sig


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def on(signal_name: str, handler, weak=False):
    """Subscribe *handler* to the event identified by *signal_name*.

    Handlers must accept a *sender* positional argument (the dotted event
    name string) followed by the keyword payload, e.g.::

        def my_handler(sender, **kwargs): ...
    """
    _resolve(signal_name).connect(handler, weak=weak)
    return handler


def emit(signal_name: str, **kwargs):
    """Emit the event identified by *signal_name* with keyword payload.

    Receivers connected via :func:`on` or ``signal.connect`` will receive
    the dotted event name string as the *sender* positional argument.
    """
    sig = _resolve(signal_name)
    sig.send(signal_name, **kwargs)
