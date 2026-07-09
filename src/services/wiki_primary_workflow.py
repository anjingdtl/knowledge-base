"""Phase 4C primary canonical workflow."""
from __future__ import annotations

from typing import Any

from src.services.wiki_canary_workflow import WikiCanaryWorkflow


class WikiPrimaryWorkflow(WikiCanaryWorkflow):
    """Formal canonical v2 workflow for primary mode.

    Primary reuses canary's conservative merge guards and projection parity
    checks, but it is not gated by the canary allowlist.
    """

    def __init__(
        self,
        block_repository: Any,
        extractor: Any,
        matcher: Any,
        repository: Any,
        projection: Any,
        config: Any = None,
        merge_engine: Any | None = None,
        clock=None,
        perf_counter=None,
    ) -> None:
        super().__init__(
            block_repository=block_repository,
            extractor=extractor,
            matcher=matcher,
            repository=repository,
            projection=projection,
            config=config,
            merge_engine=merge_engine,
            clock=clock,
            perf_counter=perf_counter,
            allow_all=True,
            report_dir_name="primary_reports",
        )
