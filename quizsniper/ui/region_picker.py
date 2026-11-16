"""区域框选器。

做法：把「即将被截图的那一块」原样显示出来让用户拖框，而不是盖一层全屏透明窗。
好处是所见即所得——用户拖的矩形和最终裁切像素一一对应，也不会受 Tk 窗口坐标系
与 DPI 缩放不一致的影响（只做图像坐标换算，不做窗口坐标换算）。

缩放只影响显示，确认时按 ``1/zoom`` 还原成原始像素坐标。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Tuple

from PIL import Image, ImageTk

from .theme import PALETTE, ui_font

Rect = Tuple[int, int, int, int]


class RegionPicker(tk.Toplevel):
    def __init__(self, master: tk.Misc, image: Image.Image,
                 on_confirm: Callable[[Rect], None],
                 title: str = "框选识别区域",
                 note: str = ""):
        super().__init__(master)
        self.on_confirm = on_confirm
        self.image = image
        self._rect: Optional[Rect] = None
        self._start: Optional[Tuple[int, int]] = None
        self._items = {}

        self.title(title)
        self.configure(bg=PALETTE["bg"])
        self.transient(master)
        self.resizable(False, False)

        # ---- 计算显示缩放 ----
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        max_w, max_h = int(screen_w * 0.86), int(screen_h * 0.78)
        zoom = min(1.0, max_w / max(1, image.width), max_h / max(1, image.height))
        self.zoom = max(0.05, zoom)
        display_w = max(120, int(image.width * self.zoom))
        display_h = max(90, int(image.height * self.zoom))

        header = tk.Frame(self, bg=PALETTE["bg"])
        header.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(header, text=title, bg=PALETTE["bg"], fg=PALETTE["text"],
                 font=ui_font(12, "bold")).pack(side="left")
        tk.Label(header, text=f"原图 {image.width}×{image.height}"
                              f"{' · 显示缩放 %d%%' % round(self.zoom * 100) if self.zoom < 1 else ''}",
                 bg=PALETTE["bg"], fg=PALETTE["faint"], font=ui_font(9)).pack(side="left", padx=10)
        if note:
            tk.Label(header, text=note, bg=PALETTE["bg"], fg=PALETTE["muted"],
                     font=ui_font(9)).pack(side="right")

        self.canvas = tk.Canvas(self, width=display_w, height=display_h, bg=PALETTE["surface"],
                                highlightthickness=1, highlightbackground=PALETTE["border"],
                                cursor="crosshair")
        self.canvas.pack(padx=14, pady=(0, 8))

        self._photo = ImageTk.PhotoImage(
            image.resize((display_w, display_h), Image.LANCZOS) if self.zoom < 1 else image)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

        # 遮罩：选区外的四个矩形
        self._mask = PALETTE["bg"]
        for key in ("top", "bottom", "left", "right"):
            self._items[key] = self.canvas.create_rectangle(
                0, 0, 0, 0, fill=self._mask, outline="", stipple="gray75", state="hidden")
        self._items["sel"] = self.canvas.create_rectangle(
            0, 0, 0, 0, outline=PALETTE["accent"], width=2, dash=(6, 4), state="hidden")
        self._items["hint"] = self.canvas.create_text(
            display_w // 2, display_h // 2, text="按住鼠标左键拖动，框住题目区域",
            fill=PALETTE["text"], font=ui_font(11))

        self.info_var = tk.StringVar(value="未选择区域")
        footer = tk.Frame(self, bg=PALETTE["bg"])
        footer.pack(fill="x", padx=14, pady=(0, 12))
        tk.Label(footer, textvariable=self.info_var, bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=ui_font(9), anchor="w").pack(side="left")
        ttk.Button(footer, text="取消", style="Ghost.TButton",
                   command=self._cancel).pack(side="right")
        self._ok_button = ttk.Button(footer, text="确定区域", style="Accent.TButton",
                                     command=self._confirm, state="disabled")
        self._ok_button.pack(side="right", padx=(0, 8))
        ttk.Button(footer, text="清除", style="Ghost.TButton",
                   command=self._clear).pack(side="right", padx=(0, 8))

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._confirm())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.update_idletasks()
        self._center(master)
        self.grab_set()
        self.focus_force()

    # ------------------------------------------------------------------
    def _center(self, master: tk.Misc) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        x = master.winfo_rootx() + max(0, (master.winfo_width() - width) // 2)
        y = master.winfo_rooty() + max(0, (master.winfo_height() - height) // 3)
        x = max(0, min(x, self.winfo_screenwidth() - width))
        y = max(0, min(y, self.winfo_screenheight() - height))
        self.geometry(f"+{x}+{y}")

    def _on_press(self, event) -> None:
        self._start = (event.x, event.y)
        self._rect = None
        self.canvas.itemconfigure(self._items["hint"], state="hidden")
        self._ok_button.configure(state="disabled")

    def _on_drag(self, event) -> None:
        if self._start is None:
            return
        x0, y0 = self._start
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        self._draw_selection(left, top, right, bottom)
        self._update_info(left, top, right, bottom)

    def _on_release(self, event) -> None:
        if self._start is None:
            return
        x0, y0 = self._start
        left, right = sorted((x0, event.x))
        top, bottom = sorted((y0, event.y))
        self._start = None
        if right - left < 8 or bottom - top < 8:
            self._clear()
            return
        self._rect = (left, top, right, bottom)
        self._ok_button.configure(state="normal")

    def _draw_selection(self, left: int, top: int, right: int, bottom: int) -> None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        self.canvas.coords(self._items["top"], 0, 0, width, top)
        self.canvas.coords(self._items["bottom"], 0, bottom, width, height)
        self.canvas.coords(self._items["left"], 0, top, left, bottom)
        self.canvas.coords(self._items["right"], right, top, width, bottom)
        for key in ("top", "bottom", "left", "right"):
            self.canvas.itemconfigure(self._items[key], state="normal")
        self.canvas.coords(self._items["sel"], left, top, right, bottom)
        self.canvas.itemconfigure(self._items["sel"], state="normal")

    def _update_info(self, left: int, top: int, right: int, bottom: int) -> None:
        scale = self.zoom
        crop = (round(left / scale), round(top / scale),
                round(right / scale), round(bottom / scale))
        width = crop[2] - crop[0]
        height = crop[3] - crop[1]
        self.info_var.set(f"选区 {width}×{height} px  ·  左上角 ({crop[0]}, {crop[1]})")

    # ------------------------------------------------------------------
    def _clear(self) -> None:
        self._rect = None
        for key in ("top", "bottom", "left", "right", "sel"):
            self.canvas.itemconfigure(self._items[key], state="hidden")
        self.canvas.itemconfigure(self._items["hint"], state="normal")
        self.info_var.set("未选择区域")
        self._ok_button.configure(state="disabled")

    def _confirm(self) -> None:
        if not self._rect:
            return
        scale = self.zoom
        left, top, right, bottom = self._rect
        crop = (max(0, round(left / scale)), max(0, round(top / scale)),
                min(self.image.width, round(right / scale)),
                min(self.image.height, round(bottom / scale)))
        if crop[2] - crop[0] < 8 or crop[3] - crop[1] < 8:
            self._clear()
            return
        callback = self.on_confirm
        self.grab_release()
        self.destroy()
        callback(crop)

    def _cancel(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
