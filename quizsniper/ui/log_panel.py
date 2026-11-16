"""日志面板：实时滚动、级别过滤、导出。"""

from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Optional

from ..logbus import LogBus, Record
from .theme import PALETTE, attach_scroll, mono_font, text_widget, ui_font
from .widgets import check

LEVEL_COLORS = {
    "debug": PALETTE["faint"],
    "info": PALETTE["muted"],
    "ok": PALETTE["ok"],
    "warn": PALETTE["warn"],
    "error": PALETTE["danger"],
}
LEVEL_ORDER = {"debug": 10, "info": 20, "ok": 25, "warn": 30, "error": 40}


class LogPanel(tk.Frame):
    def __init__(self, master: tk.Misc, log: LogBus):
        super().__init__(master, bg=PALETTE["bg"])
        self.log = log
        self._queue: "queue.Queue[Record]" = queue.Queue()
        self._unsubscribe = log.subscribe(self._queue.put)
        self._filter = "info"

        bar = tk.Frame(self, bg=PALETTE["bg"])
        bar.pack(fill="x", pady=(10, 8))
        tk.Label(bar, text="级别", bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=ui_font(9)).pack(side="left")
        self.level_var = tk.StringVar(value="info")
        level_box = ttk.Combobox(bar, textvariable=self.level_var, state="readonly", width=8,
                                 values=["debug", "info", "ok", "warn", "error"])
        level_box.pack(side="left", padx=6)
        level_box.bind("<<ComboboxSelected>>", lambda _e: self._set_filter())
        self.autoscroll_var = tk.BooleanVar(value=True)
        check(bar, "跟随最新", self.autoscroll_var,
              bg=PALETTE["bg"]).pack(side="left", padx=8)
        ttk.Button(bar, text="清空", style="Ghost.TButton",
                   command=self._clear).pack(side="right")
        ttk.Button(bar, text="导出", style="Ghost.TButton",
                   command=self._export).pack(side="right", padx=(0, 8))

        holder = tk.Frame(self, bg=PALETTE["bg"])
        holder.pack(fill="both", expand=True, pady=(0, 10))
        self.text = text_widget(holder, height=18, mono=True, wrap="word")
        self.text.pack(side="left", fill="both", expand=True)
        attach_scroll(holder, self.text).pack(side="right", fill="y")

        for level, color in LEVEL_COLORS.items():
            self.text.tag_configure(level, foreground=color)
        self.text.tag_configure("time", foreground=PALETTE["faint"])

        for record in log.snapshot():
            self._append(record)

        self.after(120, self._drain)

    # ------------------------------------------------------------------
    def _set_filter(self) -> None:
        self._filter = self.level_var.get()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for record in self.log.snapshot():
            self._append(record)
        self.text.configure(state="disabled")

    def _drain(self) -> None:
        try:
            while True:
                record = self._queue.get_nowait()
                self._append(record)
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.after(120, self._drain)
        except Exception:
            pass

    def _append(self, record: Record) -> None:
        if LEVEL_ORDER.get(record.level, 20) < LEVEL_ORDER.get(self._filter, 20):
            return
        editable = str(self.text.cget("state")) == "normal"
        self.text.configure(state="normal")
        self.text.insert("end", f"{record.clock}  ", "time")
        self.text.insert("end", f"{record.message}\n", record.level)
        if self.autoscroll_var.get():
            self.text.see("end")
        if not editable:
            self.text.configure(state="disabled")

    def _clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="导出日志", defaultextension=".log",
            initialfile=time.strftime("quizsniper-%Y%m%d-%H%M.log"),
            filetypes=[("日志文件", "*.log"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.text.get("1.0", "end"))
        except Exception:
            pass

    def destroy(self) -> None:
        try:
            self._unsubscribe()
        except Exception:
            pass
        super().destroy()
