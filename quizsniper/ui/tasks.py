"""把耗时操作丢到后台线程，结果回到 Tk 主线程。

Tk 不是线程安全的，后台线程直接改控件会随机崩。这里统一用 queue + ``after`` 轮询，
所有回调都在主线程执行。
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional


class TaskRunner:
    def __init__(self, widget, interval_ms: int = 90):
        self.widget = widget
        self.interval_ms = interval_ms
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._job: Optional[str] = None
        self._closed = False
        self._schedule()

    # ------------------------------------------------------------------
    def start(self, func: Callable[[], Any],
              on_done: Optional[Callable[[Any], None]] = None,
              on_error: Optional[Callable[[BaseException], None]] = None,
              name: str = "task") -> None:
        def worker() -> None:
            try:
                result = func()
            except BaseException as exc:  # noqa: BLE001 - 需要把异常带回主线程
                self._queue.put(("error", on_error, exc))
            else:
                self._queue.put(("done", on_done, result))

        threading.Thread(target=worker, name=name, daemon=True).start()

    # ------------------------------------------------------------------
    def _schedule(self) -> None:
        if self._closed:
            return
        try:
            self._job = self.widget.after(self.interval_ms, self._poll)
        except Exception:
            self._closed = True

    def _poll(self) -> None:
        try:
            while True:
                kind, callback, payload = self._queue.get_nowait()
                if callback is None:
                    continue
                try:
                    callback(payload)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self._schedule()

    def close(self) -> None:
        self._closed = True
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
