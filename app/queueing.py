from __future__ import annotations

import queue
import threading
from collections import deque
from typing import Any


class PlaybackQueue:
    """Bounded thread-safe deque with ADD/NEXT priority semantics."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self._items: deque[dict[str, Any]] = deque()
        self._condition = threading.Condition()

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def full(self) -> bool:
        with self._condition:
            return len(self._items) >= self.maxsize

    def put_nowait(self, item: dict[str, Any], *, next_item: bool = False) -> None:
        with self._condition:
            if len(self._items) >= self.maxsize:
                raise queue.Full
            if next_item:
                self._items.appendleft(item)
            else:
                self._items.append(item)
            self._condition.notify()

    def wait_for_item(self) -> None:
        """Block until at least one item exists without removing it."""
        with self._condition:
            while not self._items:
                self._condition.wait()

    def get_nowait(self) -> dict[str, Any]:
        """Remove the next item or raise queue.Empty."""
        with self._condition:
            if not self._items:
                raise queue.Empty
            return self._items.popleft()

    def get(self) -> dict[str, Any]:
        """Compatibility blocking get used by unit tests and simple callers."""
        self.wait_for_item()
        return self.get_nowait()

    def clear(self) -> list[dict[str, Any]]:
        with self._condition:
            removed = list(self._items)
            self._items.clear()
            return removed


def normalize_queue_mode(data: dict[str, Any], *, default: str = "add") -> str:
    """Normalize legacy replace and HA enqueue values into one queue mode."""
    mode = str(data.get("queue_mode") or data.get("enqueue") or "").strip().lower()
    if not mode:
        if "replace" in data:
            return "replace" if bool(data.get("replace")) else default
        return default
    if mode not in {"add", "next", "play", "replace"}:
        raise ValueError("queue_mode must be add, next, play or replace")
    return mode
