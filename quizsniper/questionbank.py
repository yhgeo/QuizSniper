"""题库加载、去重、增删改与持久化。

分层约定：
* ``question_banks/*.json`` —— 只读题库（内置/导入的资料），界面不直接改写；
* ``answer_question_bank.json`` —— 用户可写题库，新增/编辑/删除都落在这里。

旧版把两处数据平铺进同一个列表，改完再整体回写容易把只读题库一起覆盖，
这里用 ``source`` 与 ``writable`` 标记区分，保存时只写用户库。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import matcher
from .logbus import LogBus

USER_BANK_FILENAME = "answer_question_bank.json"


def _make_id(question: str, answer: str) -> str:
    import hashlib

    digest = hashlib.sha1(f"{question}\x00{answer}".encode("utf-8")).hexdigest()
    return digest[:12]


@dataclass
class BankEntry:
    question: str
    answer: str
    aliases: List[str] = field(default_factory=list)
    source: str = "user"
    writable: bool = True
    entry_id: str = ""
    candidates: List[matcher.Candidate] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.entry_id:
            self.entry_id = _make_id(self.question, self.answer)
        if not self.candidates:
            self.rebuild()

    def rebuild(self) -> None:
        self.candidates = matcher.build_candidates(self.question, self.aliases)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"question": self.question, "answer": self.answer}
        if self.aliases:
            payload["aliases"] = list(self.aliases)
        return payload

    def preview(self, limit: int = 60) -> str:
        text = " ".join(self.question.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class LoadReport:
    entries: List[BankEntry] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duplicates: int = 0
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.entries)

    def summary(self) -> str:
        if not self.ok:
            return "题库为空"
        return f"{len(self.entries)} 题 / {len(self.files)} 个文件"


class QuestionBank:
    def __init__(self, bank_dir: Optional[Path] = None,
                 user_file: Optional[Path] = None,
                 log: Optional[LogBus] = None):
        from . import config as config_module

        self.bank_dir = Path(bank_dir) if bank_dir else config_module.bank_dir_path()
        self.user_file = Path(user_file) if user_file else config_module.bank_file_path()
        self.log = log
        self.entries: List[BankEntry] = []
        self.report = LoadReport()

    # ------------------------------------------------------------------
    def load(self) -> LoadReport:
        started = time.time()
        entries: List[BankEntry] = []
        files: List[str] = []
        errors: List[str] = []

        if self.bank_dir.exists() and self.bank_dir.is_dir():
            for path in sorted(self.bank_dir.glob("*.json")):
                loaded, error = self._load_file(path, writable=False, source=path.name)
                if error:
                    errors.append(f"{path.name}: {error}")
                    continue
                entries.extend(loaded)
                files.append(f"{path.name}({len(loaded)})")

        if self.user_file.exists():
            loaded, error = self._load_file(self.user_file, writable=True,
                                            source=USER_BANK_FILENAME)
            if error:
                errors.append(f"{USER_BANK_FILENAME}: {error}")
            else:
                entries.extend(loaded)
                if loaded:
                    files.append(f"{USER_BANK_FILENAME}({len(loaded)})")

        deduped, removed = self._dedupe(entries)
        self.entries = deduped
        self.report = LoadReport(entries=deduped, files=files, errors=errors,
                                 duplicates=removed, elapsed=time.time() - started)

        if self.log:
            if errors:
                self.log.warn("题库读取告警: " + "；".join(errors))
            if deduped:
                self.log.info(f"题库已加载 {len(deduped)} 条"
                              f"（去重 {removed} 条，用时 {self.report.elapsed:.2f}s）")
            else:
                self.log.warn("未找到可用题库，请把 JSON 放进 question_banks/ 目录")
        return self.report

    def _load_file(self, path: Path, writable: bool,
                   source: str) -> Tuple[List[BankEntry], str]:
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            return [], str(exc)
        return self._parse_data(data, writable, source), ""

    @staticmethod
    def _parse_data(data: Any, writable: bool, source: str) -> List[BankEntry]:
        if isinstance(data, list):
            raw_entries = data
        elif isinstance(data, dict):
            raw_entries = data.get("entries", [])
        else:
            return []
        if not isinstance(raw_entries, list):
            return []

        result: List[BankEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "") or "").strip()
            answer = str(item.get("answer", "") or "").strip()
            aliases_raw = item.get("aliases", [])
            if isinstance(aliases_raw, str):
                aliases_raw = [aliases_raw]
            aliases = [str(a).strip() for a in aliases_raw or []
                       if str(a).strip()] if isinstance(aliases_raw, (list, tuple)) else []
            if not question or not answer:
                continue
            result.append(BankEntry(question=question, answer=answer, aliases=aliases,
                                    source=source, writable=writable))
        return result

    @staticmethod
    def _dedupe(entries: Sequence[BankEntry]) -> Tuple[List[BankEntry], int]:
        """按规范化题干去重，保留先出现的（只读题库优先）。"""
        seen: Dict[str, BankEntry] = {}
        result: List[BankEntry] = []
        removed = 0
        for entry in entries:
            key = matcher.normalize(entry.question)
            if not key:
                continue
            existing = seen.get(key)
            if existing is not None:
                # 合并别名，不丢信息
                for alias in entry.aliases:
                    if alias not in existing.aliases:
                        existing.aliases.append(alias)
                        existing.rebuild()
                removed += 1
                continue
            seen[key] = entry
            result.append(entry)
        return result, removed

    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        writable = sum(1 for e in self.entries if e.writable)
        return {
            "total": len(self.entries),
            "writable": writable,
            "readonly": len(self.entries) - writable,
            "files": list(self.report.files),
            "errors": list(self.report.errors),
        }

    def search(self, keyword: str, limit: int = 500) -> List[BankEntry]:
        needle = matcher.normalize(keyword)
        if not needle:
            return self.entries[:limit]
        result: List[BankEntry] = []
        for entry in self.entries:
            haystacks = [entry.question, entry.answer, *entry.aliases]
            if any(needle in matcher.normalize(h) for h in haystacks):
                result.append(entry)
                if len(result) >= limit:
                    break
        return result

    def get(self, entry_id: str) -> Optional[BankEntry]:
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    # ---- 增删改（只动用户库）----
    def add(self, question: str, answer: str, aliases: Sequence[str] = ()) -> BankEntry:
        question, answer = question.strip(), answer.strip()
        if not question or not answer:
            raise ValueError("题目与答案都不能为空")
        key = matcher.normalize(question)
        for entry in self.entries:
            if matcher.normalize(entry.question) == key:
                if not entry.writable:
                    raise ValueError(f"该题目来自只读题库（{entry.source}），请修改对应文件")
                entry.answer = answer
                entry.aliases = [a for a in aliases if a]
                entry.rebuild()
                self.save_user_bank()
                return entry
        entry = BankEntry(question=question, answer=answer,
                          aliases=[a for a in aliases if a],
                          source=USER_BANK_FILENAME, writable=True)
        self.entries.append(entry)
        self.save_user_bank()
        return entry

    def update(self, entry_id: str, question: str, answer: str,
               aliases: Sequence[str] = ()) -> BankEntry:
        entry = self.get(entry_id)
        if entry is None:
            raise ValueError("题目不存在")
        if not entry.writable:
            raise ValueError(f"该题目来自只读题库（{entry.source}），请直接改文件")
        if not question.strip() or not answer.strip():
            raise ValueError("题目与答案都不能为空")
        entry.question = question.strip()
        entry.answer = answer.strip()
        entry.aliases = [a for a in aliases if a]
        entry.rebuild()
        self.save_user_bank()
        return entry

    def delete(self, entry_id: str) -> bool:
        entry = self.get(entry_id)
        if entry is None:
            return False
        if not entry.writable:
            raise ValueError(f"该题目来自只读题库（{entry.source}），无法删除")
        self.entries.remove(entry)
        self.save_user_bank()
        return True

    def add_many(self, items: Iterable[Dict[str, Any]]) -> int:
        """批量导入（不逐条落盘，末尾统一保存）。"""
        added = 0
        for item in items:
            question = str(item.get("question", "") or "").strip()
            answer = str(item.get("answer", "") or "").strip()
            if not question or not answer:
                continue
            key = matcher.normalize(question)
            if any(matcher.normalize(e.question) == key for e in self.entries):
                continue
            aliases_raw = item.get("aliases", [])
            aliases = [str(a) for a in aliases_raw] if isinstance(aliases_raw, (list, tuple)) else []
            self.entries.append(BankEntry(question=question, answer=answer, aliases=aliases,
                                          source=USER_BANK_FILENAME, writable=True))
            added += 1
        if added:
            self.save_user_bank()
        return added

    def import_file(self, path: Path) -> Tuple[int, int, str]:
        """导入外部 JSON。返回 (新增, 跳过, 错误)。"""
        try:
            with Path(path).open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            return 0, 0, str(exc)
        entries = self._parse_data(data, writable=True, source=Path(path).name)
        if not entries:
            return 0, 0, "文件里没有有效题目"
        existing = {matcher.normalize(e.question) for e in self.entries}
        added = skipped = 0
        for entry in entries:
            if matcher.normalize(entry.question) in existing:
                skipped += 1
                continue
            entry.source, entry.writable = USER_BANK_FILENAME, True
            self.entries.append(entry)
            existing.add(matcher.normalize(entry.question))
            added += 1
        if added:
            self.save_user_bank()
        return added, skipped, ""

    # ------------------------------------------------------------------
    def save_user_bank(self) -> Path:
        payload = {"entries": [e.to_dict() for e in self.entries if e.writable]}
        self.user_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.user_file.parent),
                                   prefix=".bank-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.user_file)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        if self.log:
            self.log.ok(f"题库已保存：{len(payload['entries'])} 条 → {self.user_file.name}")
        return self.user_file

    def export_all(self, path: Path) -> int:
        payload = {"entries": [e.to_dict() for e in self.entries]}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return len(payload["entries"])
