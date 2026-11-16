"""轻量日志总线：控制台 + 内存环形缓冲 + 订阅者回调。

GUI 用它把后台线程的日志推到界面上，CLI 用它打印。所有写入线程安全。
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

LEVELS = {"debug": 10, "info": 20, "ok": 25, "warn": 30, "error": 40}


@dataclass(frozen=True)
class Record:
    ts: float
    level: str
    message: str

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.ts))


class LogBus:
    def __init__(self, capacity: int = 3000, echo: bool = True, min_level: str = "info"):
        self._records: Deque[Record] = deque(maxlen=capacity)
        self._listeners: List[Callable[[Record], None]] = []
        self._lock = threading.RLock()
        self._echo = echo
        self._min = LEVELS.get(min_level, 20)

    # ---- 订阅 ----
    def subscribe(self, callback: Callable[[Record], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def snapshot(self) -> List[Record]:
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    # ---- 写入 ----
    def log(self, level: str, message: str) -> None:
        level = level if level in LEVELS else "info"
        if LEVELS[level] < self._min:
            return
        record = Record(time.time(), level, str(message))
        with self._lock:
            self._records.append(record)
            listeners = list(self._listeners)
        if self._echo:
            prefix = {"debug": "·", "info": "·", "ok": "✓", "warn": "!", "error": "×"}[level]
            stream = sys.stderr if level == "error" else sys.stdout
            try:
                print(f"[{record.clock}] {prefix} {record.message}", file=stream, flush=True)
            except Exception:
                pass
        for listener in listeners:
            try:
                listener(record)
            except Exception:
                pass

    def debug(self, message: str) -> None:
        self.log("debug", message)

    def info(self, message: str) -> None:
        self.log("info", message)

    def ok(self, message: str) -> None:
        self.log("ok", message)

    def warn(self, message: str) -> None:
        self.log("warn", message)

    def error(self, message: str) -> None:
        self.log("error", message)


_default: Optional[LogBus] = None


def default_bus() -> LogBus:
    global _default
    if _default is None:
        _default = LogBus()
    return _default
