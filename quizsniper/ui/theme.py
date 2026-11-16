"""深色主题：调色板、字体探测与 ttk 样式注册。

统一用 ``clam`` 主题做基底，Windows 原生主题不允许改背景色，深色界面上会花。
所有颜色从 :data:`PALETTE` 取，组件里不再写死色值。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

PALETTE = {
    "bg": "#0e1116",
    "surface": "#151a21",
    "surface_alt": "#1c222b",
    "surface_hi": "#232a35",
    "border": "#28303c",
    "text": "#e8ecf3",
    "muted": "#8992a3",
    "faint": "#5d6675",
    "accent": "#4d8dff",
    "accent_soft": "#1b2b4d",
    "ok": "#3ddc97",
    "ok_soft": "#12332a",
    "warn": "#f5b942",
    "warn_soft": "#3a2d12",
    "danger": "#ff6b6b",
    "danger_soft": "#3a1a1c",
}

FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "HarmonyOS Sans SC",
                   "PingFang SC", "Noto Sans SC", "Segoe UI")

_state = {"font": "Microsoft YaHei UI", "mono": "Consolas"}


def font_family() -> str:
    return _state["font"]


def mono_family() -> str:
    return _state["mono"]


def ui_font(size: int = 10, weight: str = "normal") -> tuple:
    return (font_family(), size, weight) if weight != "normal" else (font_family(), size)


def mono_font(size: int = 9) -> tuple:
    return (_state["mono"], size)


def _detect_fonts(root: tk.Misc) -> None:
    try:
        families = set(tkfont.families(root))
    except Exception:
        return
    for candidate in FONT_CANDIDATES:
        if candidate in families:
            _state["font"] = candidate
            break
    for mono in ("Cascadia Mono", "JetBrains Mono", "Consolas", "Courier New"):
        if mono in families:
            _state["mono"] = mono
            break


def apply_scaling(root: tk.Misc) -> None:
    """按系统 DPI 校正 Tk 缩放，避免 125%/150% 缩放下字体过小。

    注意不能用 ``GetDpiForSystem()``：在 125% 缩放下它仍可能返回 96，
    而 Tk 自己按 ``tk scaling`` 计算字体点值。这里取 Win10+ 的
    ``GetDpiForWindow``，拿不到就保留 Tk 默认值。
    """
    try:
        import ctypes

        hwnd = root.winfo_id()
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        if dpi and dpi > 0:
            root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
    except Exception:
        pass


def _safe_configure(style: ttk.Style, name: str, **options) -> None:
    """不同 Tk 版本支持的 style 选项不一致，不认的直接跳过。"""
    for key, value in options.items():
        try:
            style.configure(name, **{key: value})
        except tk.TclError:
            continue


def apply_theme(root: tk.Tk) -> ttk.Style:
    _detect_fonts(root)
    p = PALETTE
    base_font = ui_font(10)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=p["bg"])

    style.configure(".", background=p["bg"], foreground=p["text"],
                    font=base_font, borderwidth=0, focuscolor=p["accent"])
    style.configure("TFrame", background=p["bg"])
    style.configure("Surface.TFrame", background=p["surface"])
    style.configure("Alt.TFrame", background=p["surface_alt"])

    style.configure("TLabel", background=p["surface"], foreground=p["text"], font=base_font)
    style.configure("Bg.TLabel", background=p["bg"], foreground=p["text"])
    style.configure("Title.TLabel", background=p["bg"], foreground=p["text"],
                    font=ui_font(16, "bold"))
    style.configure("Subtitle.TLabel", background=p["bg"], foreground=p["muted"], font=ui_font(9))
    style.configure("CardTitle.TLabel", background=p["surface"], foreground=p["text"],
                    font=ui_font(11, "bold"))
    style.configure("Muted.TLabel", background=p["surface"], foreground=p["muted"], font=ui_font(9))
    style.configure("Field.TLabel", background=p["surface"], foreground=p["muted"], font=ui_font(9))
    style.configure("Answer.TLabel", background=p["surface"], foreground=p["accent"],
                    font=ui_font(20, "bold"))
    style.configure("Hint.TLabel", background=p["surface"], foreground=p["faint"], font=ui_font(9))

    # ---- 按钮 ----
    style.configure("TButton", background=p["surface_alt"], foreground=p["text"],
                    borderwidth=1, bordercolor=p["border"], focusthickness=0,
                    padding=(12, 6), relief="flat", font=base_font)
    style.map("TButton",
              background=[("active", p["surface_hi"]), ("disabled", p["surface"])],
              foreground=[("disabled", p["faint"])],
              bordercolor=[("active", p["accent"])])

    style.configure("Accent.TButton", background=p["accent"], foreground="#ffffff",
                    borderwidth=0, padding=(16, 8), font=ui_font(10, "bold"))
    style.map("Accent.TButton",
              background=[("active", "#6ba1ff"), ("disabled", p["surface_hi"])],
              foreground=[("disabled", p["faint"])])

    style.configure("Ghost.TButton", background=p["surface"], foreground=p["muted"],
                    borderwidth=1, bordercolor=p["border"], padding=(10, 5))
    style.map("Ghost.TButton", background=[("active", p["surface_alt"])],
              foreground=[("active", p["text"])])

    style.configure("Danger.TButton", background=p["danger_soft"], foreground=p["danger"],
                    bordercolor=p["danger"], padding=(10, 5))
    style.map("Danger.TButton", background=[("active", "#4a2023")])

    # ---- 输入 ----
    for name in ("TEntry", "TSpinbox"):
        style.configure(name, fieldbackground=p["surface_alt"], foreground=p["text"],
                        insertcolor=p["text"], bordercolor=p["border"],
                        lightcolor=p["border"], darkcolor=p["border"], padding=5)
        style.map(name, bordercolor=[("focus", p["accent"])],
                  fieldbackground=[("disabled", p["surface"])])

    style.configure("TCombobox", fieldbackground=p["surface_alt"], background=p["surface_alt"],
                    foreground=p["text"], arrowcolor=p["muted"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"], padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", p["surface_alt"]), ("disabled", p["surface"])],
              foreground=[("disabled", p["faint"])],
              bordercolor=[("focus", p["accent"])],
              selectbackground=[("readonly", p["surface_alt"])],
              selectforeground=[("readonly", p["text"])])
    root.option_add("*TCombobox*Listbox*Background", p["surface_alt"])
    root.option_add("*TCombobox*Listbox*Foreground", p["text"])
    root.option_add("*TCombobox*Listbox*selectBackground", p["accent"])
    root.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox*Font", base_font)

    # ---- 勾选 / 滑块 ----
    style.configure("TCheckbutton", background=p["surface"], foreground=p["text"],
                    focuscolor=p["surface"])
    _safe_configure(style, "TCheckbutton",
                    indicatorbackground=p["surface_alt"], indicatorforeground=p["accent"],
                    indicatormargin=(0, 0, 6, 0))
    style.map("TCheckbutton",
              background=[("active", p["surface"])],
              indicatorbackground=[("selected", p["accent"]), ("active", p["surface_hi"])],
              indicatorforeground=[("selected", "#ffffff")])

    style.configure("TRadiobutton", background=p["surface"], foreground=p["text"],
                    focuscolor=p["surface"])
    style.map("TRadiobutton", background=[("active", p["surface"])],
              indicatorbackground=[("selected", p["accent"])])

    style.configure("TScale", background=p["surface"], troughcolor=p["surface_alt"],
                    bordercolor=p["border"], lightcolor=p["accent"], darkcolor=p["accent"])

    # ---- 容器 ----
    style.configure("TNotebook", background=p["bg"], borderwidth=0, tabmargins=(2, 6, 2, 0))
    style.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"],
                    padding=(16, 8), borderwidth=0, font=base_font,
                    bordercolor=p["surface"], lightcolor=p["surface"],
                    darkcolor=p["surface"], focuscolor=p["surface"])
    style.map("TNotebook.Tab",
              background=[("selected", p["surface"])],
              foreground=[("selected", p["text"])],
              expand=[("selected", (0, 0, 0, 0))])

    style.configure("TLabelframe", background=p["surface"], bordercolor=p["border"],
                    borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", background=p["surface"], foreground=p["muted"],
                    font=ui_font(9))

    style.configure("TSeparator", background=p["border"])

    style.configure("TScrollbar", background=p["surface_alt"], troughcolor=p["bg"],
                    bordercolor=p["bg"], arrowcolor=p["muted"], width=10)
    style.map("TScrollbar", background=[("active", p["surface_hi"])])

    style.configure("Treeview", background=p["surface_alt"], fieldbackground=p["surface_alt"],
                    foreground=p["text"], borderwidth=0, rowheight=28, font=ui_font(9))
    style.map("Treeview",
              background=[("selected", p["accent_soft"])],
              foreground=[("selected", p["text"])])
    style.configure("Treeview.Heading", background=p["surface"], foreground=p["muted"],
                    borderwidth=0, relief="flat", font=ui_font(9, "bold"), padding=(6, 6))
    style.map("Treeview.Heading", background=[("active", p["surface_hi"])])

    style.configure("TProgressbar", background=p["accent"], troughcolor=p["surface_alt"],
                    bordercolor=p["surface_alt"], lightcolor=p["accent"], darkcolor=p["accent"])

    style.configure("TPanedwindow", background=p["bg"])
    style.configure("Sash", background=p["border"], gripcount=0)

    return style


def text_widget(parent: tk.Misc, height: int = 5, wrap: str = "word",
                mono: bool = False, editable: bool = True) -> tk.Text:
    """统一风格的 Text。"""
    p = PALETTE
    widget = tk.Text(
        parent, height=height, wrap=wrap, bg=p["surface_alt"], fg=p["text"],
        insertbackground=p["text"], selectbackground=p["accent"],
        selectforeground="#ffffff", relief="flat", highlightthickness=1,
        highlightbackground=p["border"], highlightcolor=p["accent"],
        padx=8, pady=6, font=mono_font(9) if mono else ui_font(10), undo=editable,
        spacing1=1, spacing3=1,
    )
    if not editable:
        widget.configure(state="disabled", cursor="arrow")
    return widget


def attach_scroll(container: tk.Misc, widget: tk.Text, style: str = "TScrollbar") -> ttk.Scrollbar:
    bar = ttk.Scrollbar(container, orient="vertical", command=widget.yview, style=style)
    widget.configure(yscrollcommand=bar.set)
    return bar
