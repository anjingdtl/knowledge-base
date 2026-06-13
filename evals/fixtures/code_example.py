"""Example module for testing code retrieval."""


def calculate_rrf_score(ranks: list[int], k: int = 60) -> float:
    """Calculate Reciprocal Rank Fusion score.

    Args:
        ranks: List of rank positions (0-indexed)
        k: RRF constant, default 60

    Returns:
        Combined RRF score as float between 0 and 1
    """
    return sum(1.0 / (k + r + 1) for r in ranks)


class HybridSearchError(Exception):
    """Raised when hybrid search encounters a fatal error.

    This typically indicates that both vector and FTS backends
    have failed. Check embedding service and FTS5 availability.
    """
    pass


def normalize_path(path: str) -> str:
    """Normalize file path for cross-platform comparison.

    Uses os.path.normcase and normpath to handle Windows
    backslashes and case-insensitive filesystems.
    """
    import os
    return os.path.normcase(os.path.normpath(path))
