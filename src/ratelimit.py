"""공유 무료 모델 한도를 방문자 한 명이 다 쓰지 못하게 하는 간단한 호출 제한 (메모리 기반)."""
import threading
import time
from collections import deque


class SlidingWindowLimiter:
    """window 초 동안 최대 limit 회까지 허용한다. 스레드 안전."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def try_acquire(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            while self._hits and now - self._hits[0] >= self.window:
                self._hits.popleft()
            if len(self._hits) >= self.limit:
                return False
            self._hits.append(now)
            return True

    def retry_after(self, now: float | None = None) -> int:
        """다음 호출이 가능해질 때까지 남은 초 (허용 상태면 0)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) < self.limit:
                return 0
            return max(1, int(self._hits[0] + self.window - now) + 1)
