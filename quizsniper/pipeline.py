"""答题流水线：截图 → 离线 OCR → 题库匹配 → 大模型兜底。

把原来散在 ``main()`` 里的分支收进一个可被 GUI / CLI 共用的函数，
并把每一步耗时、命中方式、候选题目都结构化返回，界面才能画出来。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from . import capture as capture_module
from .config import AppConfig
from .llm import ChatResult, LLMClient
from .logbus import LogBus
from .matcher import BankMatcher, MatchResult, TopCandidate
from .ocr import OcrEngine, OcrResult
from .questionbank import LoadReport, QuestionBank


@dataclass
class SolveResult:
    ok: bool = False
    answer: str = ""
    source: str = ""                 # 题库 | AI·文本 | AI·图片
    question: str = ""
    error: str = ""
    detail: str = ""
    mode: str = ""                   # exact / contains / substring / similarity / miss
    score: float = 0.0
    candidates: List[TopCandidate] = field(default_factory=list)
    ocr_variant: str = ""
    ocr_score: float = 0.0
    timings: Dict[str, float] = field(default_factory=dict)
    image_size: Tuple[int, int] = (0, 0)
    match: Optional[MatchResult] = None

    @property
    def total(self) -> float:
        return sum(self.timings.values())


@dataclass
class SolverStats:
    total: int = 0
    bank_hits: int = 0
    ai_hits: int = 0
    misses: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.time)

    def reset(self) -> None:
        self.total = 0
        self.bank_hits = 0
        self.ai_hits = 0
        self.misses = 0
        self.errors = 0
        self.started_at = time.time()

    def as_dict(self) -> Dict[str, Any]:
        hit = self.bank_hits + self.ai_hits
        elapsed = max(0.001, time.time() - self.started_at)
        return {
            "total": self.total,
            "bank": self.bank_hits,
            "ai": self.ai_hits,
            "miss": self.misses,
            "error": self.errors,
            "hit_rate": (hit / self.total * 100.0) if self.total else 0.0,
            "speed": (elapsed / self.total) if self.total else 0.0,
        }


class Solver:
    """把配置、OCR、题库、模型串起来。线程安全（内部加锁）。"""

    def __init__(self, cfg: AppConfig, log: Optional[LogBus] = None):
        self.cfg = cfg
        self.log = log or LogBus(echo=False)
        self.ocr = OcrEngine(scale=cfg.ocr_scale, binarize=cfg.ocr_binarize,
                             auto_invert=cfg.ocr_auto_invert)
        self.bank = QuestionBank(log=self.log)
        self.matcher = BankMatcher(
            similarity_threshold=cfg.similarity_threshold,
            substring_threshold=cfg.substring_threshold,
            min_query_length=cfg.min_query_length,
            min_contains_length=cfg.min_contains_length,
        )
        self.llm = LLMClient(cfg, self.log)
        self.stats = SolverStats()
        self._lock = threading.RLock()
        self.bank_report: Optional[LoadReport] = None
        self._last_image: Optional[Image.Image] = None

    # ------------------------------------------------------------------
    def apply_config(self, cfg: AppConfig) -> None:
        with self._lock:
            self.cfg = cfg
            self.llm.update_config(cfg)
            self.matcher.configure(cfg.similarity_threshold, cfg.substring_threshold,
                                   cfg.min_query_length, cfg.min_contains_length)
            self.ocr.scale = max(1.0, min(4.0, float(cfg.ocr_scale)))
            self.ocr.binarize = bool(cfg.ocr_binarize)
            self.ocr.auto_invert = bool(cfg.ocr_auto_invert)

    def reload_bank(self) -> LoadReport:
        with self._lock:
            self.bank_report = self.bank.load()
            self.matcher.set_entries(self.bank.entries)
            return self.bank_report

    def warm_ocr(self) -> Tuple[bool, str]:
        return self.ocr.warmup()

    @property
    def bank_ready(self) -> bool:
        return bool(self.bank.entries) and self.ocr.ready

    def bank_status(self) -> str:
        if not self.bank.entries:
            return "题库为空"
        if not self.ocr.ready:
            return f"OCR 未就绪（{self.ocr.reason}）"
        return f"{len(self.bank.entries)} 题就绪"

    # ------------------------------------------------------------------
    def grab(self) -> Tuple[Optional[Image.Image], str]:
        window = capture_module.find_window(self.cfg.window_title) if self.cfg.window_title else None
        if self.cfg.window_title and window is None:
            return None, f"未找到窗口「{self.cfg.window_title}」，可能已关闭"

        # 窗口丢了还留着「跟随窗口」的区域模式会直接报错，这里降级到全屏/绝对坐标
        mode = self.cfg.region_mode
        if window is None and mode == "window":
            mode = "screen" if len(self.cfg.region) == 4 else "none"

        image, error = capture_module.capture(window, mode, self.cfg.region,
                                              self.cfg.all_screens)
        if image is not None:
            self._last_image = image
        return image, error

    @property
    def last_image(self) -> Optional[Image.Image]:
        return self._last_image

    # ------------------------------------------------------------------
    def solve(self, image: Image.Image, use_bank: Optional[bool] = None,
              use_ai: Optional[bool] = None) -> SolveResult:
        use_bank = self.cfg.question_bank_enabled if use_bank is None else use_bank
        use_ai = self.cfg.model_ready() if use_ai is None else use_ai

        result = SolveResult(image_size=getattr(image, "size", (0, 0)))
        ocr_result: Optional[OcrResult] = None

        # ---- 1. 题库路径 ----
        if use_bank and self.bank.entries:
            if not self.ocr.ready:
                ok, reason = self.warm_ocr()
                if not ok:
                    result.detail = f"OCR 不可用：{reason}"
            if self.ocr.ready:
                ocr_result = self.ocr.recognize(image)
                result.timings["ocr"] = ocr_result.elapsed
                result.ocr_variant = ocr_result.variant
                result.ocr_score = ocr_result.mean_score
                result.question = ocr_result.text

                if ocr_result.ok:
                    match = self.matcher.match(ocr_result.text)
                    result.timings["match"] = match.elapsed
                    result.match = match
                    result.mode = match.mode
                    result.score = round(match.score, 4)
                    result.candidates = match.top

                    if match.hit:
                        result.ok = True
                        result.answer = match.answer
                        result.source = "题库"
                        result.detail = f"{match.label()} · 相似度 {match.score:.2f}"
                        self._count(bank_hit=True)
                        return result
                    result.detail = f"题库未命中（最高 {match.score:.2f}）"
                else:
                    result.detail = "OCR 未识别到文字"

        # ---- 2. AI 路径 ----
        if not use_ai:
            if not result.detail:
                result.detail = "题库未开启且模型未配置"
            self._count(miss=True)
            return result

        chat: ChatResult
        if result.question:
            chat = self.llm.answer_from_text(result.question, self.cfg.default_background_prompt)
            result.timings["ai_text"] = chat.elapsed
            if chat.ok:
                result.ok = True
                result.answer = chat.text
                result.source = "AI·文本"
                result.detail = "OCR 文本直答"
                self._count(ai_hit=True)
                return result
            # 参数/鉴权类错误换图片也没用，直接判失败
            if chat.category in ("auth", "config"):
                result.error = chat.error
                result.detail = "模型鉴权失败，请检查 API Key / Base URL"
                self._count(error=True)
                return result
            result.detail = f"文本答题失败（{chat.error[:80]}），改用图片重试"

        chat = self.llm.answer_from_image(image, self.cfg.default_background_prompt)
        result.timings["ai_image"] = chat.elapsed
        if chat.ok:
            result.ok = True
            result.answer = chat.text
            result.source = "AI·图片"
            result.detail = "图片直答" if not result.question else "文本失败后图片兜底"
            self._count(ai_hit=True)
            return result

        result.error = chat.error
        if not result.detail:
            result.detail = "模型返回失败"
        hard_failure = chat.category in ("auth", "config")
        self._count(error=hard_failure, miss=not hard_failure)
        return result

    def capture_and_solve(self, use_bank: Optional[bool] = None,
                          use_ai: Optional[bool] = None) -> SolveResult:
        image, error = self.grab()
        if image is None:
            self._count(error=True)
            return SolveResult(error=error, detail="截图失败")
        return self.solve(image, use_bank=use_bank, use_ai=use_ai)

    # ------------------------------------------------------------------
    def _count(self, bank_hit: bool = False, ai_hit: bool = False,
               miss: bool = False, error: bool = False) -> None:
        self.stats.total += 1
        if bank_hit:
            self.stats.bank_hits += 1
        if ai_hit:
            self.stats.ai_hits += 1
        if miss:
            self.stats.misses += 1
        if error:
            self.stats.errors += 1
