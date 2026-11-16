"""设置面板：模型、题库阈值、OCR、热键、请求参数。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from ..config import PROVIDER_NAMES, PROVIDER_PRESETS, AppConfig, default_base_url
from ..logbus import LogBus
from .tasks import TaskRunner
from .theme import PALETTE, attach_scroll, mono_font, text_widget, ui_font
from .widgets import Card, FormGrid, ScrollFrame, check, toast


class SettingsPanel(tk.Frame):
    def __init__(self, master: tk.Misc, cfg: AppConfig, log: LogBus,
                 on_save: Callable[[AppConfig], None],
                 on_test: Optional[Callable[[AppConfig], None]] = None):
        super().__init__(master, bg=PALETTE["bg"])
        self.log = log
        self.on_save = on_save
        self.on_test = on_test
        self.runner = TaskRunner(self)
        self._models: list = []

        scroll = ScrollFrame(self, bg=PALETTE["bg"])
        scroll.pack(fill="both", expand=True, pady=(10, 0))
        holder = scroll.inner
        holder.configure(bg=PALETTE["bg"])

        # ---------------- 模型 ----------------
        model_card = Card(holder, title="大模型", hint="OpenAI 兼容接口，未配置时只用题库")
        model_card.pack(fill="x", pady=(0, 12), padx=2)
        grid = FormGrid(model_card.body)
        grid.pack(fill="x")

        self.provider_var = tk.StringVar()
        self.provider_box = ttk.Combobox(grid, textvariable=self.provider_var,
                                         values=PROVIDER_NAMES, state="readonly")
        self.provider_box.grid()
        grid.add("提供方", self.provider_box)
        self.provider_box.bind("<<ComboboxSelected>>", self._on_provider_change)

        self.base_url_var = tk.StringVar()
        grid.add("Base URL", ttk.Entry(grid, textvariable=self.base_url_var))

        self.api_key_var = tk.StringVar()
        key_row = tk.Frame(grid, bg=PALETTE["surface"])
        self.key_entry = ttk.Entry(key_row, textvariable=self.api_key_var, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True)
        self._key_visible = False
        ttk.Button(key_row, text="显示", style="Ghost.TButton", width=6,
                   command=self._toggle_key).pack(side="left", padx=(6, 0))
        grid.add("API Key", key_row, hint="只存在本地 answer_helper_config.json，不会上传")

        self.model_var = tk.StringVar()
        model_row = tk.Frame(grid, bg=PALETTE["surface"])
        self.model_box = ttk.Combobox(model_row, textvariable=self.model_var, values=[])
        self.model_box.pack(side="left", fill="x", expand=True)
        self.fetch_button = ttk.Button(model_row, text="拉取模型", style="Ghost.TButton",
                                       command=self._fetch_models)
        self.fetch_button.pack(side="left", padx=(6, 0))
        grid.add("模型 ID", model_row, hint="可手动填写；不支持 /models 的提供方直接输入即可")

        self.model_enabled_var = tk.BooleanVar(value=True)
        check(grid, "启用大模型答题（题库未命中时兜底）",
              self.model_enabled_var).grid(
            row=grid._row, column=0, columnspan=2, sticky="w", pady=(6, 4))
        grid._row += 1

        # ---------------- 题库 ----------------
        bank_card = Card(holder, title="题库匹配", hint="阈值越低越容易命中，也越容易误答")
        bank_card.pack(fill="x", pady=(0, 12), padx=2)
        bank_grid = FormGrid(bank_card.body)
        bank_grid.pack(fill="x")

        self.bank_enabled_var = tk.BooleanVar(value=True)
        check(bank_grid, "启用题库优先", self.bank_enabled_var).grid(
            row=bank_grid._row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        bank_grid._row += 1

        self.sim_var = tk.StringVar(value="0.72")
        self.sub_var = tk.StringVar(value="0.80")
        self.min_query_var = tk.StringVar(value="8")
        self.min_contains_var = tk.StringVar(value="8")
        bank_grid.add("整体相似阈值", ttk.Spinbox(bank_grid, from_=0.50, to=0.99, increment=0.01,
                                              textvariable=self.sim_var, width=10),
                      hint="difflib 编辑相似度，建议 0.70~0.80")
        bank_grid.add("子串覆盖阈值", ttk.Spinbox(bank_grid, from_=0.50, to=0.99, increment=0.01,
                                              textvariable=self.sub_var, width=10),
                      hint="OCR 漏字/多字时用「较短串被覆盖比例」判定，建议 0.75~0.85")
        bank_grid.add("最短查询长度", ttk.Spinbox(bank_grid, from_=4, to=40, increment=1,
                                              textvariable=self.min_query_var, width=10),
                      hint="规范化后短于该长度不做模糊匹配，防止「下列说法正确的是」误命中")
        bank_grid.add("最短包含长度", ttk.Spinbox(bank_grid, from_=4, to=60, increment=1,
                                              textvariable=self.min_contains_var, width=10))

        # ---------------- OCR ----------------
        ocr_card = Card(holder, title="离线 OCR", hint="RapidOCR，本地运行，无需联网")
        ocr_card.pack(fill="x", pady=(0, 12), padx=2)
        ocr_grid = FormGrid(ocr_card.body)
        ocr_grid.pack(fill="x")
        self.ocr_scale_var = tk.StringVar(value="2.0")
        ocr_grid.add("放大倍数", ttk.Spinbox(ocr_grid, from_=1.0, to=4.0, increment=0.5,
                                          textvariable=self.ocr_scale_var, width=10),
                     hint="小字号截图放大后识别更准，过大会变慢")
        self.ocr_binarize_var = tk.BooleanVar(value=False)
        self.ocr_invert_var = tk.BooleanVar(value=True)
        check(ocr_grid, "二值化预处理（仅在高对比度截图下开启）",
              self.ocr_binarize_var).grid(
            row=ocr_grid._row, column=0, columnspan=2, sticky="w", pady=2)
        ocr_grid._row += 1
        check(ocr_grid, "暗底自动反色（深色主题界面建议开启）",
              self.ocr_invert_var).grid(
            row=ocr_grid._row, column=0, columnspan=2, sticky="w", pady=2)
        ocr_grid._row += 1

        # ---------------- 请求与交互 ----------------
        misc_card = Card(holder, title="请求与交互")
        misc_card.pack(fill="x", pady=(0, 12), padx=2)
        misc_grid = FormGrid(misc_card.body)
        misc_grid.pack(fill="x")
        self.timeout_var = tk.StringVar(value="30")
        self.max_tokens_var = tk.StringVar(value="256")
        self.temperature_var = tk.StringVar(value="0.1")
        misc_grid.add("请求超时(秒)", ttk.Spinbox(misc_grid, from_=5, to=180, increment=5,
                                             textvariable=self.timeout_var, width=10))
        misc_grid.add("输出 token 上限", ttk.Spinbox(misc_grid, from_=32, to=2048, increment=32,
                                                textvariable=self.max_tokens_var, width=10))
        misc_grid.add("temperature", ttk.Spinbox(misc_grid, from_=0.0, to=1.0, increment=0.05,
                                                 textvariable=self.temperature_var, width=10))

        self.hotkey_var = tk.StringVar(value="F8")
        misc_grid.add("全局热键", ttk.Entry(misc_grid, textvariable=self.hotkey_var),
                      hint="例：F8 / Ctrl+F8 / Alt+Shift+Q，改完保存即时生效")
        self.interval_var = tk.StringVar(value="2.0")
        misc_grid.add("连续识别间隔(秒)", ttk.Spinbox(misc_grid, from_=0.5, to=30.0, increment=0.5,
                                               textvariable=self.interval_var, width=10))
        self.copy_var = tk.BooleanVar(value=False)
        check(misc_grid, "识别成功后自动复制答案到剪贴板",
              self.copy_var).grid(row=misc_grid._row, column=0, columnspan=2,
                                  sticky="w", pady=4)
        misc_grid._row += 1

        self.bg_var = tk.StringVar(value="")
        bg_label = tk.Label(misc_card.body, text="默认答题背景提示词", bg=PALETTE["surface"],
                            fg=PALETTE["muted"], font=ui_font(9), anchor="w")
        bg_label.pack(fill="x", pady=(10, 4))
        bg_holder = tk.Frame(misc_card.body, bg=PALETTE["surface"])
        bg_holder.pack(fill="x")
        self.bg_text = text_widget(bg_holder, height=3)
        self.bg_text.pack(side="left", fill="both", expand=True)
        attach_scroll(bg_holder, self.bg_text).pack(side="right", fill="y")

        # ---------------- 底部按钮 ----------------
        footer = tk.Frame(holder, bg=PALETTE["bg"])
        footer.pack(fill="x", pady=(0, 16), padx=2)
        ttk.Button(footer, text="保存设置", style="Accent.TButton",
                   command=self._save).pack(side="right")
        ttk.Button(footer, text="测试连接", style="Ghost.TButton",
                   command=self._test).pack(side="right", padx=(0, 8))
        ttk.Button(footer, text="重载配置", style="Ghost.TButton",
                   command=self._reload).pack(side="right", padx=(0, 8))

        self.status_var = tk.StringVar(value="")
        tk.Label(footer, textvariable=self.status_var, bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=ui_font(9), anchor="w").pack(side="left")

        self.load(cfg)

    # ------------------------------------------------------------------
    def load(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self.provider_var.set(cfg.provider_name or PROVIDER_NAMES[0])
        self.base_url_var.set(cfg.base_url or (default_base_url(cfg.provider_name) or ""))
        self.api_key_var.set(cfg.api_key)
        self.model_var.set(cfg.model)
        self.model_enabled_var.set(bool(cfg.model_enabled))
        self.bank_enabled_var.set(bool(cfg.question_bank_enabled))
        self.sim_var.set(f"{cfg.similarity_threshold:.2f}")
        self.sub_var.set(f"{cfg.substring_threshold:.2f}")
        self.min_query_var.set(str(cfg.min_query_length))
        self.min_contains_var.set(str(cfg.min_contains_length))
        self.ocr_scale_var.set(f"{cfg.ocr_scale:g}")
        self.ocr_binarize_var.set(bool(cfg.ocr_binarize))
        self.ocr_invert_var.set(bool(cfg.ocr_auto_invert))
        self.timeout_var.set(str(cfg.request_timeout))
        self.max_tokens_var.set(str(cfg.max_tokens))
        self.temperature_var.set(f"{cfg.temperature:g}")
        self.hotkey_var.set(cfg.hotkey)
        self.interval_var.set(f"{cfg.auto_interval:g}")
        self.copy_var.set(bool(cfg.copy_to_clipboard))
        self.bg_text.delete("1.0", "end")
        self.bg_text.insert("1.0", cfg.default_background_prompt)
        self.status_var.set("")

    def _reload(self) -> None:
        self.load(AppConfig.load())
        self.status_var.set("已重新读取磁盘配置")

    def _toggle_key(self) -> None:
        self._key_visible = not self._key_visible
        self.key_entry.configure(show="" if self._key_visible else "•")

    def _on_provider_change(self, _event=None) -> None:
        name = self.provider_var.get()
        url = default_base_url(name)
        current = self.base_url_var.get().strip()
        if url and (not current or any(current == u for _, u in PROVIDER_PRESETS if u)):
            self.base_url_var.set(url)

    # ------------------------------------------------------------------
    def collect(self) -> AppConfig:
        cfg = self._cfg.copy()
        cfg.provider_name = self.provider_var.get().strip()
        cfg.base_url = self.base_url_var.get().strip().rstrip("/")
        cfg.api_key = self.api_key_var.get().strip()
        cfg.model = self.model_var.get().strip()
        cfg.model_enabled = bool(self.model_enabled_var.get())
        cfg.question_bank_enabled = bool(self.bank_enabled_var.get())
        cfg.similarity_threshold = _to_float(self.sim_var.get(), 0.72, 0.3, 0.999)
        cfg.substring_threshold = _to_float(self.sub_var.get(), 0.80, 0.3, 0.999)
        cfg.min_query_length = _to_int(self.min_query_var.get(), 8, 1, 200)
        cfg.min_contains_length = _to_int(self.min_contains_var.get(), 8, 1, 400)
        cfg.ocr_scale = _to_float(self.ocr_scale_var.get(), 2.0, 1.0, 4.0)
        cfg.ocr_binarize = bool(self.ocr_binarize_var.get())
        cfg.ocr_auto_invert = bool(self.ocr_invert_var.get())
        cfg.request_timeout = _to_int(self.timeout_var.get(), 30, 3, 300)
        cfg.max_tokens = _to_int(self.max_tokens_var.get(), 256, 16, 8192)
        cfg.temperature = _to_float(self.temperature_var.get(), 0.1, 0.0, 2.0)
        cfg.hotkey = self.hotkey_var.get().strip() or "F8"
        cfg.auto_interval = _to_float(self.interval_var.get(), 2.0, 0.2, 120.0)
        cfg.copy_to_clipboard = bool(self.copy_var.get())
        cfg.default_background_prompt = self.bg_text.get("1.0", "end").strip()
        return cfg

    def _save(self) -> None:
        cfg = self.collect()
        try:
            cfg.save()
        except Exception as exc:
            self.status_var.set(f"保存失败: {exc}")
            toast(self, f"保存失败: {exc}", "error")
            return
        self._cfg = cfg
        self.on_save(cfg)
        self.status_var.set("已保存")
        toast(self, "设置已保存", "ok")

    def _test(self) -> None:
        cfg = self.collect()
        if not cfg.base_url or not cfg.api_key:
            self.status_var.set("请先填 Base URL 与 API Key")
            return
        self.status_var.set("正在测试…")
        self._models_from(cfg, test=True)

    def _fetch_models(self) -> None:
        cfg = self.collect()
        if not cfg.base_url or not cfg.api_key:
            toast(self, "请先填 Base URL 与 API Key", "warn")
            return
        self.fetch_button.configure(state="disabled")
        self.status_var.set("正在拉取模型列表…")
        self._models_from(cfg, test=False)

    def _models_from(self, cfg: AppConfig, test: bool) -> None:
        def work():
            from ..llm import LLMClient

            client = LLMClient(cfg, self.log)
            try:
                return client.fetch_models()
            finally:
                client.close()

        def done(result):
            models, error = result
            self.fetch_button.configure(state="normal")
            if error:
                self.status_var.set(error)
                toast(self, error, "warn" if test else "error")
                return
            self._models = models
            self.model_box.configure(values=models)
            self.status_var.set(f"连接正常，可用模型 {len(models)} 个")
            toast(self, f"连接正常，共 {len(models)} 个模型", "ok")

        def failed(exc):
            self.fetch_button.configure(state="normal")
            self.status_var.set(f"请求异常: {exc}")
            toast(self, f"请求异常: {exc}", "error")

        self.runner.start(work, done, failed, name="fetch-models")


def _to_float(raw: str, default: float, low: float, high: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _to_int(raw: str, default: int, low: int, high: int) -> int:
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))
