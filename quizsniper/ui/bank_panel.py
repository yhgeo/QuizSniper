"""题库管理面板：检索、查看、增删改、导入导出。"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Callable, List, Optional

from ..logbus import LogBus
from ..questionbank import BankEntry, QuestionBank
from .tasks import TaskRunner
from .theme import PALETTE, attach_scroll, text_widget, ui_font
from .widgets import toast


class BankPanel(tk.Frame):
    def __init__(self, master: tk.Misc, bank: QuestionBank, log: LogBus,
                 on_changed: Callable[[], None]):
        super().__init__(master, bg=PALETTE["bg"])
        self.bank = bank
        self.log = log
        self.on_changed = on_changed
        self.runner = TaskRunner(self)
        self._rows: List[BankEntry] = []
        self._selected: Optional[BankEntry] = None
        self._search_job: Optional[str] = None

        # 用 grid 而不是 pack：表格要占满剩余高度，编辑区固定在底部。
        # pack 里先 pack 的 expand 控件会吃掉全部空间，导致编辑区按钮被裁掉。
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ---------------- 顶部工具条 ----------------
        bar = tk.Frame(self, bg=PALETTE["bg"])
        bar.grid(row=0, column=0, sticky="ew", pady=(10, 8))

        self.search_var = tk.StringVar()
        search = ttk.Entry(bar, textvariable=self.search_var, width=20)
        search.pack(side="left")
        search.bind("<KeyRelease>", self._on_search_key)
        ttk.Button(bar, text="检索", style="Ghost.TButton",
                   command=self.refresh).pack(side="left", padx=(6, 0))

        self.stats_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.stats_var, bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=ui_font(9), anchor="w").pack(side="left", padx=10, fill="x", expand=True)

        ttk.Button(bar, text="导出", style="Ghost.TButton",
                   command=self._export).pack(side="right")
        ttk.Button(bar, text="导入", style="Ghost.TButton",
                   command=self._import).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="重载", style="Ghost.TButton",
                   command=self._reload).pack(side="right", padx=(0, 8))

        # ---------------- 列表 ----------------
        table_holder = tk.Frame(self, bg=PALETTE["bg"])
        table_holder.grid(row=1, column=0, sticky="nsew")

        columns = ("answer", "source", "question")
        self.tree = ttk.Treeview(table_holder, columns=columns, show="headings",
                                 selectmode="browse")
        self.tree.heading("answer", text="答案")
        self.tree.heading("source", text="来源")
        self.tree.heading("question", text="题目")
        self.tree.column("answer", width=140, anchor="w", stretch=False)
        self.tree.column("source", width=130, anchor="w", stretch=False)
        self.tree.column("question", width=520, anchor="w", stretch=True)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_holder, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.tag_configure("readonly", foreground=PALETTE["muted"])

        # ---------------- 编辑器 ----------------
        editor = tk.Frame(self, bg=PALETTE["surface"],
                          highlightbackground=PALETTE["border"], highlightthickness=1)
        editor.grid(row=2, column=0, sticky="ew", pady=(10, 10), padx=2)

        head = tk.Frame(editor, bg=PALETTE["surface"])
        head.pack(fill="x", padx=12, pady=(10, 4))
        self.mode_var = tk.StringVar(value="未选中题目")
        tk.Label(head, textvariable=self.mode_var, bg=PALETTE["surface"], fg=PALETTE["text"],
                 font=ui_font(10, "bold")).pack(side="left")
        self.meta_var = tk.StringVar(value="")
        tk.Label(head, textvariable=self.meta_var, bg=PALETTE["surface"], fg=PALETTE["faint"],
                 font=ui_font(9)).pack(side="left", padx=8)

        body = tk.Frame(editor, bg=PALETTE["surface"])
        body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="题目", bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9)).grid(row=0, column=0, sticky="ne", padx=(0, 8), pady=4)
        q_holder = tk.Frame(body, bg=PALETTE["surface"])
        q_holder.grid(row=0, column=1, sticky="nsew", pady=4)
        self.question_text = text_widget(q_holder, height=4)
        self.question_text.pack(side="left", fill="both", expand=True)
        attach_scroll(q_holder, self.question_text).pack(side="right", fill="y")

        tk.Label(body, text="答案", bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9)).grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
        self.answer_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.answer_var).grid(row=1, column=1, sticky="ew", pady=4)

        tk.Label(body, text="别名", bg=PALETTE["surface"], fg=PALETTE["muted"],
                 font=ui_font(9)).grid(row=2, column=0, sticky="ne", padx=(0, 8), pady=4)
        a_holder = tk.Frame(body, bg=PALETTE["surface"])
        a_holder.grid(row=2, column=1, sticky="nsew", pady=4)
        self.alias_text = text_widget(a_holder, height=2)
        self.alias_text.pack(side="left", fill="both", expand=True)
        attach_scroll(a_holder, self.alias_text).pack(side="right", fill="y")
        tk.Label(body, text="每行一个，填 OCR 常见误识别版本", bg=PALETTE["surface"],
                 fg=PALETTE["faint"], font=ui_font(8), anchor="w").grid(
            row=3, column=1, sticky="w")

        actions = tk.Frame(editor, bg=PALETTE["surface"])
        actions.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(actions, text="新增题目", style="Accent.TButton",
                   command=self._new_entry).pack(side="left")
        ttk.Button(actions, text="保存修改", style="Ghost.TButton",
                   command=self._save_entry).pack(side="left", padx=6)
        ttk.Button(actions, text="删除", style="Danger.TButton",
                   command=self._delete_entry).pack(side="left")
        self.notice_var = tk.StringVar(value="改动写入用户题库")
        tk.Label(actions, textvariable=self.notice_var, bg=PALETTE["surface"],
                 fg=PALETTE["faint"], font=ui_font(8)).pack(side="right")

        self.refresh()

    # ------------------------------------------------------------------
    def set_bank(self, bank: QuestionBank) -> None:
        self.bank = bank
        self.refresh()

    def refresh(self) -> None:
        keyword = self.search_var.get().strip()
        entries = self.bank.search(keyword) if keyword else list(self.bank.entries)
        self._rows = entries[:800]
        self.tree.delete(*self.tree.get_children())
        for index, entry in enumerate(self._rows, 1):
            tags = () if entry.writable else ("readonly",)
            source = Path(entry.source).stem if entry.source.endswith(".json") else entry.source
            self.tree.insert("", "end", iid=entry.entry_id,
                             values=(entry.answer, source, entry.preview(72)),
                             tags=tags)
        stats = self.bank.stats()
        suffix = f" · 过滤到 {len(self._rows)} 条" if keyword else ""
        self.stats_var.set(f"共 {stats['total']} 题（可写 {stats['writable']} / "
                           f"只读 {stats['readonly']}）{suffix}")

    def _on_search_key(self, _event=None) -> None:
        if self._search_job:
            try:
                self.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.after(220, self.refresh)

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        entry = self.bank.get(selection[0])
        if entry is None:
            return
        self._selected = entry
        self.mode_var.set("编辑题目" if entry.writable else "只读题目")
        self.meta_var.set(f"{entry.source} · {entry.entry_id}")
        self.question_text.configure(state="normal")
        self.question_text.delete("1.0", "end")
        self.question_text.insert("1.0", entry.question)
        if not entry.writable:
            self.question_text.configure(state="disabled")
        self.answer_var.set(entry.answer)
        self.alias_text.configure(state="normal")
        self.alias_text.delete("1.0", "end")
        self.alias_text.insert("1.0", "\n".join(entry.aliases))
        if not entry.writable:
            self.alias_text.configure(state="disabled")

    # ------------------------------------------------------------------
    def _new_entry(self) -> None:
        self._selected = None
        self.mode_var.set("新增题目")
        self.meta_var.set("")
        self.question_text.configure(state="normal")
        self.question_text.delete("1.0", "end")
        self.alias_text.configure(state="normal")
        self.alias_text.delete("1.0", "end")
        self.answer_var.set("")
        self.notice_var.set("填写题干与答案后点「保存修改」即写入用户题库")
        self.question_text.focus_set()

    def _collect_editor(self):
        question = self.question_text.get("1.0", "end").strip()
        answer = self.answer_var.get().strip()
        aliases = [line.strip() for line in self.alias_text.get("1.0", "end").splitlines()
                   if line.strip()]
        return question, answer, aliases

    def _save_entry(self) -> None:
        question, answer, aliases = self._collect_editor()
        if not question or not answer:
            toast(self, "题干与答案不能为空", "warn")
            return
        try:
            if self._selected is None:
                entry = self.bank.add(question, answer, aliases)
            else:
                entry = self.bank.update(self._selected.entry_id, question, answer, aliases)
        except Exception as exc:
            toast(self, str(exc), "error")
            self.notice_var.set(str(exc))
            return
        self.notice_var.set(f"已保存：{entry.preview(40)}")
        self.refresh()
        self.on_changed()
        toast(self, "题目已保存", "ok")

    def _delete_entry(self) -> None:
        if self._selected is None:
            toast(self, "请先选中一行", "warn")
            return
        if not messagebox.askyesno("删除确认",
                                   f"确定删除这道题？\n\n{self._selected.preview(60)}",
                                   parent=self):
            return
        try:
            self.bank.delete(self._selected.entry_id)
        except Exception as exc:
            toast(self, str(exc), "error")
            return
        self._selected = None
        self.refresh()
        self.on_changed()
        toast(self, "已删除", "ok")

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        report = self.bank.load()
        self.refresh()
        self.on_changed()
        if report.errors:
            toast(self, "部分文件读取失败：" + report.errors[0], "warn")
        else:
            toast(self, f"题库已重载：{len(report.entries)} 题", "ok")

    def _import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择题库 JSON",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        added, skipped, error = self.bank.import_file(Path(path))
        if error:
            toast(self, f"导入失败: {error}", "error")
            return
        self.refresh()
        self.on_changed()
        toast(self, f"导入完成：新增 {added}，跳过重复 {skipped}", "ok")

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="导出题库", defaultextension=".json",
            initialfile="quizsniper_bank_export.json",
            filetypes=[("JSON 文件", "*.json")])
        if not path:
            return
        try:
            count = self.bank.export_all(Path(path))
        except Exception as exc:
            toast(self, f"导出失败: {exc}", "error")
            return
        toast(self, f"已导出 {count} 条", "ok")
