"""Shared structured-query pagination (fetch limit+1, honest totals)."""
from __future__ import annotations

from typing import Any


def paginate_structured_rows(
    rows: list[Any],
    *,
    effective_limit: int,
    offset: int,
) -> tuple[list[Any], dict[str, Any]]:
    """Paginate rows fetched with ``effective_limit + 1`` probe row.

    Returns ``(page, meta)`` where::

        meta = {
          limit, offset, next_offset, truncated,
          total_estimate, total_estimate_is_exact
        }

    Rules:
      - ``meta.limit == effective_limit``
      - ``has_more = len(rows) > effective_limit``
      - never treat page length as an exact total
      - ``next_offset = offset + len(page)`` only when has_more
    """
    effective_limit = max(0, int(effective_limit))
    offset = max(0, int(offset))
    rows = list(rows or [])

    has_more = len(rows) > effective_limit
    page = rows[:effective_limit]
    next_offset = offset + len(page) if has_more and page else None
    if next_offset is not None and next_offset <= offset:
        next_offset = None
        has_more = False

    meta = {
        "limit": effective_limit,
        "offset": offset,
        "next_offset": next_offset,
        "truncated": has_more,
        # Without a separate COUNT(*), page length is not a total.
        "total_estimate": None,
        "total_estimate_is_exact": False,
    }
    return page, meta
