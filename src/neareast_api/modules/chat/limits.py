"""Small in-memory request limit per client, to protect the paid LLM key.

One process only (no shared store): enough for a single demo server. Behind a
reverse proxy every client shares the proxy's address unless the server is run
with forwarded headers enabled, so set the limit with that in mind.
"""

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, per_minute: int, window_seconds: float = 60.0):
        self.per_minute = per_minute
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> float:
        """Record a request. Returns 0 when allowed, else seconds until the next slot."""
        if self.per_minute <= 0:
            return 0.0
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return max(self.window - (now - hits[0]), 0.001)
            hits.append(now)
            if len(self._hits) > 10_000:  # forget idle clients
                for idle in [k for k, v in self._hits.items() if not v]:
                    del self._hits[idle]
            return 0.0
