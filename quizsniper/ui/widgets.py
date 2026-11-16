"""通用界面控件：卡片、状态胶囊、表单栅格、滚动容器。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .theme import PALETTE, ui_font


class Card(tk.Frame):
    """带标题的圆角感卡片（1px 描边 + 表面色）。"""

    def __init__(self, master: tk.Misc, title: str = "", hint: str = "",
                 padding: int = 14, **kwargs):
        super().__init__(master, bg=PALETTE["surface"],
                         highlightbackground=PALETTE["border"], highlightthickness=1,
                         bd=0, **kwargs)
        self.padding = padding
        self.header = tk.Frame(self, bg=PALETTE["surface"])
        self.header.pack(fill="x", padx=padding, pady=(padding, 0))
        self.title_label: Optional[tk.Label] = None
        if title:
            self.title_label = tk.Label(self.header, text=title, bg=PALETTE["surface"],
                                        fg=PALETTE["text"], font=ui_font(11, "bold"),
                                        anchor="w")
            self.title_label.pack(side="left")
        self.hint_label: Optional[tk.Label] = None
        if hint:
            self.hint_label = tk.Label(self.header, text=hint, bg=PALETTE["surface"],
                                       fg=PALETTE["faint"], font=ui_font(9), anchor="w")
            self.hint_label.pack(side="left", padx=(8, 0))
        self.tools = tk.Frame(self.header, bg=PALETTE["surface"])
        self.tools.pack(side="right")

        self.body = tk.Frame(self, bg=PALETTE["surface"])
        self.body.pack(fill="both", expand=True, padx=padding,
                       pady=(10 if title else padding, padding))

    def set_title(self, text: str) -> None:
        if self.title_label is not None:
            self.title_label.configure(text=text)

    def set_hint(self, text: str) -> None:
        if self.hint_label is None:
            self.hint_label = tk.Label(self.header, text="", bg=PALETTE["surface"],
                                       fg=PALETTE["faint"], font=ui_font(9), anchor="w")
            self.hint_label.pack(side="left", padx=(8, 0))
        self.hint_label.configure(text=text)


class StatusPill(tk.Frame):
    """状态胶囊：idle / ok / warn / danger。"""

    COLORS = {
        "idle": (PALETTE["surface_alt"], PALETTE["muted"], PALETTE["border"]),
        "ok": (PALETTE["ok_soft"], PALETTE["ok"], "#1d4d3a"),
        "warn": (PALETTE["warn_soft"], PALETTE["warn"], "#5a4318"),
        "danger": (PALETTE["danger_soft"], PALETTE["danger"], "#5b2528"),
        "accent": (PALETTE["accent_soft"], PALETTE["accent"], "#26467d"),
    }

    def __init__(self, master: tk.Misc, text: str = "", state: str = "idle"):
        super().__init__(master, bg=PALETTE["surface"], bd=0)
        self.dot = tk.Label(self, text="●", bg=PALETTE["surface"], fg=PALETTE["faint"],
                            font=ui_font(8))
        self.dot.pack(side="left")
        self.label = tk.Label(self, text=text, bg=PALETTE["surface"], fg=PALETTE["muted"],
                              font=ui_font(9))
        self.label.pack(side="left", padx=(4, 0))
        self.set(text, state)

    def set(self, text: str, state: str = "idle") -> None:
        _, fg, _ = self.COLORS.get(state, self.COLORS["idle"])
        self.label.configure(text=text, fg=fg)
        self.dot.configure(fg=fg)


class FormGrid(tk.Frame):
    """两列表单：左标签右控件，控件列自动拉伸。"""

    def __init__(self, master: tk.Misc, label_width: int = 92, gap: int = 8):
        super().__init__(master, bg=PALETTE["surface"])
        self.columnconfigure(1, weight=1)
        self.label_width = label_width
        self.gap = gap
        self._row = 0

    def add(self, label: str, widget: tk.Widget, hint: str = "") -> tk.Widget:
        tk.Label(self, text=label, bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9), anchor="e", width=0).grid(
            row=self._row, column=0, sticky="e", padx=(0, self.gap), pady=4)
        widget.grid(row=self._row, column=1, sticky="ew", pady=4)
        self._row += 1
        if hint:
            tk.Label(self, text=hint, bg=PALETTE["surface"], fg=PALETTE["faint"],
                     font=ui_font(8), anchor="w", justify="left", wraplength=380).grid(
                row=self._row, column=1, sticky="w", pady=(0, 4))
            self._row += 1
        return widget

    def add_row(self, widget: tk.Widget, span: bool = True) -> tk.Widget:
        widget.grid(row=self._row, column=0, columnspan=2, sticky="ew", pady=4)
        self._row += 1
        return widget

    def add_block(self, label: str, widget: tk.Widget, hint: str = "") -> tk.Widget:
        tk.Label(self, text=label, bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9), anchor="w").grid(
            row=self._row, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self._row += 1
        widget.grid(row=self._row, column=0, columnspan=2, sticky="nsew", pady=(0, 2))
        self._row += 1
        if hint:
            tk.Label(self, text=hint, bg=PALETTE["surface"], fg=PALETTE["faint"],
                     font=ui_font(8), anchor="w", justify="left", wraplength=420).grid(
                row=self._row, column=0, columnspan=2, sticky="w", pady=(0, 4))
            self._row += 1
        return widget


class ScrollFrame(tk.Frame):
    """垂直滚动容器，内容放 :attr:`inner`。"""

    def __init__(self, master: tk.Misc, bg: str = PALETTE["surface"]):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass


class AnswerPanel(tk.Frame):
    """答案大字显示区，带来源与耗时小字。"""

    def __init__(self, master: tk.Misc):
        super().__init__(master, bg=PALETTE["surface"])
        self.answer_var = tk.StringVar(value="等待识别")
        self.answer_label = tk.Label(self, textvariable=self.answer_var, bg=PALETTE["surface"],
                                     fg=PALETTE["faint"], font=ui_font(22, "bold"),
                                     anchor="w", justify="left", wraplength=520)
        self.answer_label.pack(fill="x")

        self.meta = tk.Frame(self, bg=PALETTE["surface"])
        self.meta.pack(fill="x", pady=(6, 0))
        self.source_pill = StatusPill(self.meta, "待机", "idle")
        self.source_pill.pack(side="left")
        self.detail_var = tk.StringVar(value="")
        tk.Label(self.meta, textvariable=self.detail_var, bg=PALETTE["surface"],
                 fg=PALETTE["faint"], font=ui_font(9), anchor="w").pack(side="left", padx=(10, 0))

        self.bind("<Configure>", self._resize)

    def _resize(self, event) -> None:
        width = max(200, event.width - 8)
        self.answer_label.configure(wraplength=width)

    def show_answer(self, text: str, source: str = "", detail: str = "") -> None:
        self.answer_var.set(text or "—")
        self.answer_label.configure(fg=PALETTE["text"] if text else PALETTE["faint"],
                                    font=ui_font(20 if len(text) > 24 else 24, "bold"))
        state = "ok" if source == "题库" else ("accent" if source.startswith("AI") else "warn")
        self.source_pill.set(source or "未命中", state if source else "warn")
        self.detail_var.set(detail)

    def show_idle(self, text: str = "等待识别") -> None:
        self.answer_var.set(text)
        self.answer_label.configure(fg=PALETTE["faint"], font=ui_font(20, "bold"))
        self.source_pill.set("待机", "idle")
        self.detail_var.set("")


class Toast(tk.Toplevel):
    """右下角轻提示，2 秒自动消失。"""

    def __init__(self, master: tk.Misc, text: str, kind: str = "info", duration: int = 1800):
        super().__init__(master)
        color = {"info": PALETTE["accent"], "ok": PALETTE["ok"],
                 "warn": PALETTE["warn"], "error": PALETTE["danger"]}.get(kind, PALETTE["accent"])
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        frame = tk.Frame(self, bg=PALETTE["surface_alt"], highlightbackground=color,
                         highlightthickness=1)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=text, bg=PALETTE["surface_alt"], fg=PALETTE["text"],
                 font=ui_font(9), padx=14, pady=9, wraplength=320, justify="left").pack()
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{screen_w - width - 40}+{screen_h - height - 80}")
        self.after(duration, self.destroy)


def toast(master: tk.Misc, text: str, kind: str = "info") -> None:
    try:
        Toast(master, text, kind)
    except Exception:
        pass


def check(parent: tk.Misc, text: str, variable: tk.BooleanVar,
          bg: str = PALETTE["surface"]) -> tk.Checkbutton:
    """深色主题下可用的复选框。

    clam 主题的 ttk.Checkbutton 指示器改色后会把对勾一起染成底色，选中态看不出区别，
    因此这里统一用 tk.Checkbutton + selectcolor 控制，勾选状态一眼可辨。
    """
    return tk.Checkbutton(
        parent, text=text, variable=variable, bg=bg, fg=PALETTE["text"],
        activebackground=bg, activeforeground=PALETTE["text"],
        selectcolor=PALETTE["surface_alt"], highlightthickness=0, bd=0,
        anchor="w", font=ui_font(10), cursor="hand2",
    )
