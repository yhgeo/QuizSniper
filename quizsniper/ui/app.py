"""主窗口。

布局：左侧「截图来源 + 识别结果」，右侧「设置 / 题库 / 日志」，底部状态栏。
所有耗时操作（枚举窗口、OCR 预热、截图识别、拉模型、加载题库）都走后台线程，
主线程只负责画界面，避免点击后整窗假死。
"""

from __future__ import annotations

import queue
import tkinter as tk
import time
from tkinter import ttk
from typing import List, Optional

from PIL import Image, ImageTk

from .. import __version__
from .. import capture as capture_module
from ..config import AppConfig
from ..hotkey import HotkeyManager
from ..logbus import LogBus
from ..pipeline import SolveResult, Solver
from .bank_panel import BankPanel
from .log_panel import LogPanel
from .region_picker import RegionPicker
from .settings_panel import SettingsPanel
from .tasks import TaskRunner
from .theme import PALETTE, apply_scaling, apply_theme, attach_scroll, text_widget, ui_font
from .widgets import AnswerPanel, Card, StatusPill, toast


class PreviewWindow(tk.Toplevel):
    """截图预览，等比缩放到屏幕内。"""

    def __init__(self, master: tk.Misc, image: Image.Image, caption: str = ""):
        super().__init__(master)
        self.title("截图预览")
        self.configure(bg=PALETTE["bg"])
        self.transient(master)

        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        zoom = min(1.0, (screen_w * 0.8) / max(1, image.width),
                   (screen_h * 0.75) / max(1, image.height))
        display = image.resize((max(1, int(image.width * zoom)),
                                max(1, int(image.height * zoom))), Image.LANCZOS) \
            if zoom < 1 else image
        self._photo = ImageTk.PhotoImage(display)

        tk.Label(self, image=self._photo, bg=PALETTE["bg"]).pack(padx=12, pady=(12, 6))
        tk.Label(self, text=f"{image.width}×{image.height} px   {caption}",
                 bg=PALETTE["bg"], fg=PALETTE["muted"], font=ui_font(9)).pack(pady=(0, 6))
        ttk.Button(self, text="关闭", style="Ghost.TButton",
                   command=self.destroy).pack(pady=(0, 12))
        self.bind("<Escape>", lambda _e: self.destroy())


class App:
    def __init__(self, root: tk.Tk):
        capture_module.enable_dpi_awareness()

        self.root = root
        self.log = LogBus(echo=False)
        self.cfg = AppConfig.load()
        self.solver = Solver(self.cfg, self.log)
        self.hotkeys = HotkeyManager(self.log)
        self.runner = TaskRunner(root, interval_ms=80)
        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._busy = False
        self._auto_job: Optional[str] = None
        self._windows: List[capture_module.WindowInfo] = []
        self._history: List[dict] = []
        self._ocr_ready = False
        self._region_job = None

        root.title(f"QuizSniper {__version__} · OCR 题库答题助手")
        root.geometry("1280x840")
        root.minsize(1080, 720)
        apply_scaling(root)
        apply_theme(root)
        self._center_window()

        self._build_header()
        # 状态栏必须先于主体 pack，否则主体 expand 会把它的空间吃光（表现为整条不可见）
        self._build_statusbar()
        self._build_body()

        self._sync_region_label()
        self._refresh_status()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<F8>", lambda _e: self.capture())
        root.bind("<Control-s>", lambda _e: self._save_config(silent=True))
        self._pump()
        root.after(120, self._startup)

    # ------------------------------------------------------------------
    def _center_window(self) -> None:
        self.root.update_idletasks()
        width, height = 1280, 840
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=PALETTE["bg"])
        header.pack(fill="x", padx=18, pady=(14, 8))

        left = tk.Frame(header, bg=PALETTE["bg"])
        left.pack(side="left")
        title_row = tk.Frame(left, bg=PALETTE["bg"])
        title_row.pack(anchor="w")
        tk.Label(title_row, text="QuizSniper", bg=PALETTE["bg"], fg=PALETTE["text"],
                 font=ui_font(17, "bold")).pack(side="left")
        tk.Label(title_row, text=f"v{__version__}", bg=PALETTE["bg"], fg=PALETTE["faint"],
                 font=ui_font(9)).pack(side="left", padx=(8, 0), pady=(6, 0))
        tk.Label(left, text="离线 OCR · 本地题库优先 · 大模型兜底", bg=PALETTE["bg"],
                 fg=PALETTE["muted"], font=ui_font(9)).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=PALETTE["bg"])
        right.pack(side="right")
        self.pill_bank = StatusPill(right, "题库加载中", "idle")
        self.pill_bank.pack(side="left", padx=(0, 14))
        self.pill_ocr = StatusPill(right, "OCR 待预热", "idle")
        self.pill_ocr.pack(side="left", padx=(0, 14))
        self.pill_ai = StatusPill(right, "AI 未配置", "idle")
        self.pill_ai.pack(side="left")

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=PALETTE["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        body.columnconfigure(0, weight=3, minsize=600)
        body.columnconfigure(1, weight=2, minsize=420)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=PALETTE["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        right = tk.Frame(body, bg=PALETTE["bg"])
        right.grid(row=0, column=1, sticky="nsew")

        self._build_capture_card(left)
        self._build_result_card(left)
        self._build_tabs(right)

    # ---- 截图来源 ----
    def _build_capture_card(self, parent: tk.Frame) -> None:
        card = Card(parent, title="截图来源", hint="选择窗口后可按窗口相对坐标固定区域")
        card.pack(fill="x", pady=(0, 12))

        row1 = tk.Frame(card.body, bg=PALETTE["surface"])
        row1.pack(fill="x")
        tk.Label(row1, text="目标窗口", bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9), width=8, anchor="e").pack(side="left", padx=(0, 8))
        self.window_var = tk.StringVar()
        self.window_box = ttk.Combobox(row1, textvariable=self.window_var, state="readonly",
                                       values=[])
        self.window_box.pack(side="left", fill="x", expand=True)
        self.window_box.bind("<<ComboboxSelected>>", self._on_window_change)
        ttk.Button(row1, text="刷新", style="Ghost.TButton", width=6,
                   command=self.refresh_windows).pack(side="left", padx=(6, 0))
        ttk.Button(row1, text="清空", style="Ghost.TButton", width=6,
                   command=self._clear_window).pack(side="left", padx=(6, 0))

        row2 = tk.Frame(card.body, bg=PALETTE["surface"])
        row2.pack(fill="x", pady=(10, 0))
        tk.Label(row2, text="识别区域", bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9), width=8, anchor="e").pack(side="left", padx=(0, 8))
        self.region_var = tk.StringVar(value="全屏")
        tk.Label(row2, textvariable=self.region_var, bg=PALETTE["surface_alt"],
                 fg=PALETTE["text"], font=ui_font(9), anchor="w", padx=10, pady=5).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row2, text="框选", style="Ghost.TButton", width=6,
                   command=self.pick_region).pack(side="left", padx=(6, 0))
        ttk.Button(row2, text="预览", style="Ghost.TButton", width=6,
                   command=self.preview_capture).pack(side="left", padx=(6, 0))
        ttk.Button(row2, text="重置", style="Ghost.TButton", width=6,
                   command=self._reset_region).pack(side="left", padx=(6, 0))

    # ---- 识别结果 ----
    def _build_result_card(self, parent: tk.Frame) -> None:
        card = Card(parent, title="识别结果", hint="F8 随时截图")
        card.pack(fill="both", expand=True)

        self.answer_panel = AnswerPanel(card.body)
        self.answer_panel.pack(fill="x")

        tk.Frame(card.body, bg=PALETTE["border"], height=1).pack(fill="x", pady=(12, 10))

        question_head = tk.Frame(card.body, bg=PALETTE["surface"])
        question_head.pack(fill="x")
        tk.Label(question_head, text="识别题目", bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9)).pack(side="left")
        self.ocr_meta_var = tk.StringVar(value="")
        tk.Label(question_head, textvariable=self.ocr_meta_var, bg=PALETTE["surface"],
                 fg=PALETTE["faint"], font=ui_font(8)).pack(side="right")

        q_holder = tk.Frame(card.body, bg=PALETTE["surface"])
        q_holder.pack(fill="both", expand=True, pady=(6, 0))
        self.question_text = text_widget(q_holder, height=7, wrap="word", editable=False)
        self.question_text.pack(side="left", fill="both", expand=True)
        attach_scroll(q_holder, self.question_text).pack(side="right", fill="y")

        self.candidates_var = tk.StringVar(value="")
        tk.Label(card.body, textvariable=self.candidates_var, bg=PALETTE["surface"],
                 fg=PALETTE["faint"], font=ui_font(8), anchor="w", justify="left",
                 wraplength=560).pack(fill="x", pady=(8, 0))

        actions = tk.Frame(card.body, bg=PALETTE["surface"])
        actions.pack(fill="x", pady=(12, 0))
        self.capture_button = ttk.Button(actions, text="截图识别  (F8)", style="Accent.TButton",
                                         command=self.capture)
        self.capture_button.pack(side="left")
        self.auto_button = ttk.Button(actions, text="连续识别", style="Ghost.TButton",
                                      command=self.toggle_auto)
        self.auto_button.pack(side="left", padx=8)
        ttk.Button(actions, text="复制答案", style="Ghost.TButton",
                   command=self.copy_answer).pack(side="left")
        ttk.Button(actions, text="清空", style="Ghost.TButton",
                   command=self.clear_result).pack(side="left", padx=8)
        self.busy_label = tk.Label(actions, text="", bg=PALETTE["surface"],
                                   fg=PALETTE["accent"], font=ui_font(9))
        self.busy_label.pack(side="right")

    # ---- 右侧标签页 ----
    def _build_tabs(self, parent: tk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook

        settings_host = tk.Frame(notebook, bg=PALETTE["bg"])
        self.settings_panel = SettingsPanel(settings_host, self.cfg, self.log,
                                            on_save=self._on_settings_saved)
        self.settings_panel.pack(fill="both", expand=True)
        notebook.add(settings_host, text="设置")

        bank_host = tk.Frame(notebook, bg=PALETTE["bg"])
        self.bank_panel = BankPanel(bank_host, self.solver.bank, self.log,
                                    on_changed=self._on_bank_changed)
        self.bank_panel.pack(fill="both", expand=True)
        notebook.add(bank_host, text="题库")

        log_host = tk.Frame(notebook, bg=PALETTE["bg"])
        self.log_panel = LogPanel(log_host, self.log)
        self.log_panel.pack(fill="both", expand=True)
        notebook.add(log_host, text="日志")

        history_host = tk.Frame(notebook, bg=PALETTE["bg"])
        self._build_history(history_host)
        notebook.add(history_host, text="记录")

    def _build_history(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=PALETTE["bg"])
        bar.pack(fill="x", pady=(10, 8))
        self.history_stats_var = tk.StringVar(value="尚无记录")
        tk.Label(bar, textvariable=self.history_stats_var, bg=PALETTE["bg"],
                 fg=PALETTE["muted"], font=ui_font(9)).pack(side="left")
        ttk.Button(bar, text="清空记录", style="Ghost.TButton",
                   command=self._clear_history).pack(side="right")

        holder = tk.Frame(parent, bg=PALETTE["bg"])
        holder.pack(fill="both", expand=True, pady=(0, 10))
        columns = ("time", "source", "answer", "question")
        self.history_tree = ttk.Treeview(holder, columns=columns, show="headings")
        for key, title, width in (("time", "时间", 66), ("source", "来源", 74),
                                  ("answer", "答案", 120), ("question", "题目", 260)):
            self.history_tree.heading(key, text=title)
            self.history_tree.column(key, width=width, anchor="w",
                                     stretch=(key == "question"))
        self.history_tree.pack(side="left", fill="both", expand=True)
        bar2 = ttk.Scrollbar(holder, orient="vertical", command=self.history_tree.yview)
        bar2.pack(side="right", fill="y")
        self.history_tree.configure(yscrollcommand=bar2.set)
        self.history_tree.bind("<Double-1>", self._copy_history_item)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=PALETTE["surface"], height=30)
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(bar, textvariable=self.status_var, bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9), anchor="w").pack(side="left", padx=16, pady=6)
        self.hotkey_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.hotkey_var, bg=PALETTE["surface"], fg=PALETTE["faint"],
                 font=ui_font(8), anchor="e").pack(side="right", padx=16)

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------
    def _startup(self) -> None:
        self.log.info("界面已就绪")
        self.refresh_windows()
        self._reload_bank()

        def warm():
            return self.solver.warm_ocr()

        self.runner.start(warm, self._on_ocr_ready, self._on_ocr_failed, name="ocr-warmup")
        error = self.hotkeys.start(self.cfg.hotkey,
                                   lambda: self._events.put(("hotkey", None)))
        if error:
            self.log.warn(error)
            self.hotkey_var.set(f"窗口内 F8 可用 · 全局热键不可用：{error}")
        else:
            self.hotkey_var.set(f"全局热键 {self.cfg.hotkey} · 窗口内 F8 · Ctrl+S 保存设置")

    def _on_ocr_ready(self, result) -> None:
        ok, reason = result
        self._ocr_ready = bool(ok)
        if ok:
            self.log.ok(f"OCR 引擎就绪（{self.solver.ocr.warm_elapsed:.2f}s）")
        else:
            self.log.error(f"OCR 引擎不可用：{reason}")
        self._refresh_status()

    def _on_ocr_failed(self, exc: BaseException) -> None:
        self._ocr_ready = False
        self.log.error(f"OCR 预热异常：{exc}")
        self._refresh_status()

    # ------------------------------------------------------------------
    # 窗口与区域
    # ------------------------------------------------------------------
    def refresh_windows(self) -> None:
        self.status_var.set("正在枚举窗口…")

        def work():
            return capture_module.list_windows()

        def done(windows):
            self._windows = windows
            labels = [info.label() for info in windows]
            self.window_box.configure(values=labels)
            if self.cfg.window_title:
                for info in windows:
                    if info.title == self.cfg.window_title:
                        self.window_var.set(info.label())
                        break
                else:
                    self.window_var.set("")
                    self.status_var.set(f"未找到已保存的窗口：{self.cfg.window_title}")
                    return
            self.status_var.set(f"已发现 {len(windows)} 个窗口")

        def failed(exc):
            self.status_var.set(f"枚举窗口失败: {exc}")

        self.runner.start(work, done, failed, name="list-windows")

    def _current_window(self) -> Optional[capture_module.WindowInfo]:
        title = self.cfg.window_title
        if not title:
            return None
        for info in self._windows:
            if info.title == title:
                return info
        return capture_module.find_window(title)

    def _on_window_change(self, _event=None) -> None:
        label = self.window_var.get()
        for info in self._windows:
            if info.label() == label:
                self.cfg.window_title = info.title
                if self.cfg.region_mode == "none":
                    pass
                self.log.info(f"目标窗口：{info.title}")
                self._sync_region_label()
                self._save_config(silent=True)
                return

    def _clear_window(self) -> None:
        self.cfg.window_title = ""
        self.cfg.region_mode = "none"
        self.cfg.region = []
        self.window_var.set("")
        self._sync_region_label()
        self._save_config(silent=True)

    def _reset_region(self) -> None:
        self.cfg.region = []
        self.cfg.region_mode = "none"
        self._sync_region_label()
        self._save_config(silent=True)

    def _sync_region_label(self) -> None:
        region = self.cfg.region
        if self.cfg.region_mode == "window" and len(region) == 4:
            self.region_var.set(f"跟随窗口  ({region[0]}, {region[1]}) → "
                                f"({region[2]}, {region[3]})   "
                                f"{region[2] - region[0]}×{region[3] - region[1]}")
        elif self.cfg.region_mode == "screen" and len(region) == 4:
            self.region_var.set(f"屏幕绝对  ({region[0]}, {region[1]}) → "
                                f"({region[2]}, {region[3]})   "
                                f"{region[2] - region[0]}×{region[3] - region[1]}")
        elif self.cfg.window_title:
            self.region_var.set("整个窗口")
        else:
            self.region_var.set("全屏")

    def pick_region(self) -> None:
        window = self._current_window()
        image, error = capture_module.capture(
            window, "none" if window else "screen",
            [] if window else list(capture_module.virtual_screen_rect()),
            self.cfg.all_screens)
        if image is None:
            toast(self.root, f"无法获取截图：{error}", "error")
            return

        mode = "window" if window else "screen"
        origin = (window.rect[0], window.rect[1]) if window else (0, 0)

        def on_confirm(rect):
            left, top, right, bottom = rect
            if mode == "window":
                self.cfg.region = [left, top, right, bottom]
                self.cfg.region_mode = "window"
            else:
                self.cfg.region = [origin[0] + left, origin[1] + top,
                                   origin[0] + right, origin[1] + bottom]
                self.cfg.region_mode = "screen"
            self._sync_region_label()
            self._save_config(silent=True)
            self.log.ok(f"识别区域已更新：{self.cfg.region_mode} {self.cfg.region}")
            toast(self.root, "识别区域已更新", "ok")

        RegionPicker(self.root, image, on_confirm,
                     note="跟随窗口" if mode == "window" else "屏幕绝对坐标")

    def preview_capture(self) -> None:
        image, error = self.solver.grab()
        if image is None:
            toast(self.root, f"截图失败：{error}", "error")
            return
        PreviewWindow(self.root, image, caption=self.region_var.get())

    # ------------------------------------------------------------------
    # 识别
    # ------------------------------------------------------------------
    def capture(self) -> None:
        if self._busy:
            self.status_var.set("上一次识别还没结束…")
            return
        self._set_busy(True)

        def work():
            return self.solver.capture_and_solve()

        self.runner.start(work, lambda result: self._events.put(("result", result)),
                          lambda exc: self._events.put(("error", exc)), name="solve")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.capture_button.configure(state=state)
        self.busy_label.configure(text="识别中…" if busy else "")

    def _handle_result(self, result: SolveResult) -> None:
        self._set_busy(False)
        self._apply_result_ui(result)
        self._refresh_status()
        self._push_history(result)

    def _apply_result_ui(self, result: SolveResult) -> None:
        self.question_text.configure(state="normal")
        self.question_text.delete("1.0", "end")
        if result.question:
            self.question_text.insert("1.0", result.question)
        self.question_text.configure(state="disabled")

        meta_bits = []
        if result.ocr_variant:
            meta_bits.append(f"OCR 变体 {result.ocr_variant}")
        if result.ocr_score:
            meta_bits.append(f"置信度 {result.ocr_score:.2f}")
        if "ocr" in result.timings:
            meta_bits.append(f"OCR {result.timings['ocr']:.2f}s")
        if "match" in result.timings:
            meta_bits.append(f"匹配 {result.timings['match']:.2f}s")
        for key, label in (("ai_text", "AI文本"), ("ai_image", "AI图片")):
            if key in result.timings:
                meta_bits.append(f"{label} {result.timings[key]:.2f}s")
        self.ocr_meta_var.set("  ·  ".join(meta_bits))

        if result.ok:
            self.answer_panel.show_answer(result.answer, result.source, result.detail)
            self.status_var.set(f"命中：{result.source} · 总耗时 {result.total:.2f}s")
            self.log.ok(f"{result.source} → {result.answer[:60]}")
            if self.cfg.copy_to_clipboard:
                self.copy_answer(silent=True)
        else:
            reason = result.error or result.detail or "未命中"
            self.answer_panel.show_answer("未命中", "", reason[:120])
            self.answer_panel.answer_label.configure(fg=PALETTE["warn"])
            self.status_var.set(reason)
            self.log.warn(reason)

        if result.candidates:
            lines = [f"{index}. [{item.score:.2f}] {item.answer}  ·  {item.question[:52]}"
                     for index, item in enumerate(result.candidates, 1)]
            self.candidates_var.set("最接近的题库题目：\n" + "\n".join(lines))
        else:
            self.candidates_var.set("")

    def _push_history(self, result: SolveResult) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._history.insert(0, {
            "time": stamp,
            "source": result.source or ("未命中" if not result.ok else "未知"),
            "answer": result.answer or (result.error or "—"),
            "question": result.question or "",
        })
        self._history = self._history[:200]
        self._render_history()

    def _render_history(self) -> None:
        self.history_tree.delete(*self.history_tree.get_children())
        for index, item in enumerate(self._history):
            self.history_tree.insert("", "end", iid=str(index),
                                     values=(item["time"], item["source"], item["answer"],
                                             item["question"][:80]))
        stats = self.solver.stats.as_dict()
        self.history_stats_var.set(
            f"共 {stats['total']} 次 · 题库 {stats['bank']} · AI {stats['ai']} · "
            f"未命中 {stats['miss']} · 命中率 {stats['hit_rate']:.0f}%")

    def _clear_history(self) -> None:
        self._history.clear()
        self.solver.stats.reset()
        self._render_history()

    def _copy_history_item(self, _event=None) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._history):
            self._copy_text(self._history[index]["answer"])

    def copy_answer(self, silent: bool = False) -> None:
        text = self.answer_panel.answer_var.get()
        if not text or text in ("—", "等待识别", "未命中"):
            if not silent:
                toast(self.root, "还没有可复制的答案", "warn")
            return
        self._copy_text(text)
        if not silent:
            toast(self.root, "答案已复制", "ok")

    def _copy_text(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            pass

    def clear_result(self) -> None:
        self.answer_panel.show_idle()
        self.question_text.configure(state="normal")
        self.question_text.delete("1.0", "end")
        self.question_text.configure(state="disabled")
        self.candidates_var.set("")
        self.ocr_meta_var.set("")
        self.status_var.set("就绪")

    # ---- 连续识别 ----
    def toggle_auto(self) -> None:
        if self._auto_job is not None:
            self._stop_auto()
            return
        self._auto_job = self.root.after(10, self._auto_tick)
        self.auto_button.configure(text="停止连续")
        self.log.info(f"连续识别已开启，间隔 {self.cfg.auto_interval:.1f}s")

    def _stop_auto(self) -> None:
        if self._auto_job is not None:
            try:
                self.root.after_cancel(self._auto_job)
            except Exception:
                pass
            self._auto_job = None
        self.auto_button.configure(text="连续识别")
        self.log.info("连续识别已停止")

    def _auto_tick(self) -> None:
        if self._auto_job is None:
            return
        if not self._busy:
            self.capture()
        delay = max(300, int(self.cfg.auto_interval * 1000))
        self._auto_job = self.root.after(delay, self._auto_tick)

    # ------------------------------------------------------------------
    # 配置与状态
    # ------------------------------------------------------------------
    def _save_config(self, silent: bool = False) -> None:
        try:
            self.cfg.save()
        except Exception as exc:
            self.log.error(f"配置保存失败：{exc}")
            return
        if not silent:
            self.status_var.set("配置已保存")

    def _on_settings_saved(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.solver.apply_config(cfg)
        self._refresh_status()
        error = self.hotkeys.start(cfg.hotkey, lambda: self._events.put(("hotkey", None)))
        if error:
            self.hotkey_var.set(f"窗口内 F8 可用 · 全局热键不可用：{error}")
            self.log.warn(error)
        else:
            self.hotkey_var.set(f"全局热键 {cfg.hotkey} · 窗口内 F8 · Ctrl+S 保存设置")
        if not cfg.question_bank_enabled:
            self.log.info("题库已关闭，仅使用大模型")
        self._sync_region_label()

    def _on_bank_changed(self) -> None:
        self.solver.matcher.set_entries(self.solver.bank.entries)
        self._refresh_status()

    def _reload_bank(self) -> None:
        def work():
            return self.solver.reload_bank()

        def done(report):
            self.log.info(f"题库：{report.summary()}")
            if report.errors:
                self.log.warn("题库文件告警：" + "；".join(report.errors[:3]))
            self.bank_panel.refresh()
            self._refresh_status()

        def failed(exc):
            self.log.error(f"题库加载失败：{exc}")

        self.runner.start(work, done, failed, name="load-bank")

    def _refresh_status(self) -> None:
        stats = self.solver.bank.stats()
        if stats["total"]:
            self.pill_bank.set(f"题库 {stats['total']}", "ok")
        else:
            self.pill_bank.set("题库为空", "warn")

        if self.solver.ocr.ready:
            self.pill_ocr.set("OCR 就绪", "ok")
        elif self._ocr_ready:
            self.pill_ocr.set("OCR 就绪", "ok")
        else:
            self.pill_ocr.set("OCR 预热中", "idle")

        if self.solver.llm.is_ready():
            self.pill_ai.set(f"AI {self.cfg.model[:18]}", "accent")
        else:
            self.pill_ai.set("AI 未配置", "warn")

    # ------------------------------------------------------------------
    # 事件泵
    # ------------------------------------------------------------------
    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "result":
                    self._handle_result(payload)
                elif kind == "error":
                    self._set_busy(False)
                    self.log.error(f"识别异常：{payload}")
                    self.answer_panel.show_answer("出错", "", str(payload)[:120])
                    self.status_var.set(f"识别异常：{payload}")
                elif kind == "hotkey":
                    self.capture()
        except queue.Empty:
            pass
        except Exception as exc:  # 事件处理异常不能停掉泵
            self.log.error(f"事件处理异常：{exc}")
        try:
            self.root.after(80, self._pump)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def on_close(self) -> None:
        self._stop_auto()
        try:
            self.cfg.save()
        except Exception:
            pass
        self.hotkeys.stop()
        self.runner.close()
        self.solver.llm.close()
        self.root.destroy()


def launch() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    launch()
