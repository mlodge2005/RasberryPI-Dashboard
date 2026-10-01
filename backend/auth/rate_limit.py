"""Small in-memory login limiter. One PiDeck instance is one process."""

from __future__ import annotations

import threading
import time


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 8, window_seconds: int = 600) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def too_many(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = [stamp for stamp in self._events.get(key, []) if now - stamp < self.window_seconds]
            self._events[key] = events
            return len(events) >= self.max_attempts

    def add_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            events = [stamp for stamp in self._events.get(key, []) if now - stamp < self.window_seconds]
            events.append(now)
            self._events[key] = events

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
