import time
from collections import defaultdict


class SimpleRateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        self._requests[key] = [
            t for t in self._requests[key] if now - t < self._window
        ]
        if len(self._requests[key]) >= self._max:
            return False
        self._requests[key].append(now)
        return True


login_limiter = SimpleRateLimiter(max_requests=10, window_seconds=60)
