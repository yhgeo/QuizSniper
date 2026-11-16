"""题库匹配算法。

旧实现的三处硬伤，这里逐条修掉：

1. ``difflib.SequenceMatcher(None, a, b)`` 默认 ``autojunk=True``。序列长度超过 200
   时，出现频率高的元素会被当成噪声丢弃——中文题干里重复的「的」「是」正好命中，
   导致长题相似度被显著低估、卡在阈值以下。这里一律 ``autojunk=False``。
2. 子串相似度用「滑动窗口 + 每窗一次 ratio」，复杂度 O(n²·m)，797 条题库单次匹配
   要几百毫秒。改成一次 ``get_matching_blocks``，用「较短串被覆盖的字符比例」
   作为分数，复杂度降到 O(n·m) 且语义更贴近「OCR 漏字」场景。
3. 题库题干里带 ``【答案】`` 标记、选项前缀 ``A.`` 等 OCR 永远不会产出的字符，
   直接参与相似度计算相当于白扣分。规范化阶段先做 NFKC + 标记剥离，并额外生成
   「仅题干」候选，兼容「题库存题干、OCR 出题干+选项」的错位。
"""

from __future__ import annotations

import difflib
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

#: 题库里常见的「答案标记」，OCR 不会产出，参与相似度计算只会拉低分数
_MARKER_RE = re.compile(r"【\s*答\s*案\s*】|\[\s*答\s*案\s*\]|（\s*答\s*案\s*）|\(\s*答\s*案\s*\)|答\s*案\s*[:：]")

#: 行首选项前缀：A. / A、/ (A) / （A）/ a)
_OPTION_LINE_RE = re.compile(r"^[\s\u3000]*(?:[（(]\s*[A-Da-dＡ-Ｄａ-ｄ]\s*[）)]|[A-Da-dＡ-Ｄａ-ｄ]\s*[\.、．\)）:：])")


@dataclass(frozen=True)
class Candidate:
    """题库条目的一个可匹配文本（题干本体 / 题干去选项 / 别名）。"""

    text: str
    normalized: str
    char_set: FrozenSet[str]
    kind: str = "question"


@dataclass
class TopCandidate:
    question: str
    answer: str
    score: float
    mode: str


@dataclass
class MatchResult:
    entry: Optional[Any] = None
    mode: str = "miss"          # exact | contains | substring | similarity | miss | skipped
    score: float = 0.0
    candidate: str = ""
    query_length: int = 0
    elapsed: float = 0.0
    top: List[TopCandidate] = field(default_factory=list)

    @property
    def hit(self) -> bool:
        return self.entry is not None

    @property
    def answer(self) -> str:
        return getattr(self.entry, "answer", "") if self.entry else ""

    def label(self) -> str:
        return {
            "exact": "完全匹配",
            "contains": "包含命中",
            "substring": "子串相似",
            "similarity": "整体相似",
            "miss": "未命中",
            "skipped": "已跳过",
        }.get(self.mode, self.mode)


# ----------------------------------------------------------------------
# 文本规范化
# ----------------------------------------------------------------------
def normalize(text: str) -> str:
    """NFKC 归一 + 去答案标记 + 只保留字母数字与汉字。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).lower()
    t = _MARKER_RE.sub("", t)
    return "".join(ch for ch in t if ch.isalnum())


def strip_options(text: str) -> str:
    """截掉选项区，只留题干。找不到选项行就原样返回。"""
    if not text:
        return ""
    kept: List[str] = []
    for line in str(text).splitlines():
        if _OPTION_LINE_RE.match(line):
            # 首行就像选项（OCR 只截到选项），保守起见整体保留
            if kept:
                break
        kept.append(line)
    return "\n".join(kept).strip()


def build_candidates(question: str, aliases: Sequence[str] = ()) -> List[Candidate]:
    """构建去重后的候选列表。"""
    seen: set = set()
    result: List[Candidate] = []
    sources: List[Tuple[str, str]] = [(question or "", "question")]
    stem = strip_options(question or "")
    if stem and stem != (question or "").strip():
        sources.append((stem, "stem"))
    for alias in aliases or []:
        if alias:
            sources.append((str(alias), "alias"))

    for text, kind in sources:
        norm = normalize(text)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(Candidate(text=str(text).strip(), normalized=norm,
                                char_set=frozenset(norm), kind=kind))
    return result


# ----------------------------------------------------------------------
# 相似度
# ----------------------------------------------------------------------
def similarity(left: str, right: str) -> float:
    """带三级预筛的编辑相似度，等价于 difflib.ratio 但更快且不丢常用字。"""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    if matcher.real_quick_ratio() < 0.05 or matcher.quick_ratio() < 0.05:
        return 0.0
    return matcher.ratio()


def containment_score(query: str, candidate: str) -> float:
    """较短串被匹配块覆盖的比例，用来容忍 OCR 漏字/多字。"""
    if not query or not candidate:
        return 0.0
    shorter, longer = (query, candidate) if len(query) <= len(candidate) else (candidate, query)
    if shorter in longer:
        return 1.0
    matcher = difflib.SequenceMatcher(None, longer, shorter, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / max(1, len(shorter))


def char_overlap(left: FrozenSet[str], right: FrozenSet[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


# ----------------------------------------------------------------------
# 匹配器
# ----------------------------------------------------------------------
class BankMatcher:
    """有缓存的题库匹配器。条目变更后调用 :meth:`invalidate`。"""

    def __init__(self, entries: Optional[Sequence[Any]] = None,
                 similarity_threshold: float = 0.72,
                 substring_threshold: float = 0.80,
                 min_query_length: int = 8,
                 min_contains_length: int = 8,
                 cache_size: int = 256):
        self.entries: List[Any] = list(entries or [])
        self.similarity_threshold = float(similarity_threshold)
        self.substring_threshold = float(substring_threshold)
        self.min_query_length = max(1, int(min_query_length))
        self.min_contains_length = max(1, int(min_contains_length))
        self.cache_size = max(0, int(cache_size))
        self._cache: Dict[str, MatchResult] = {}
        self._order: List[str] = []

    # ---- 配置 ----
    def set_entries(self, entries: Sequence[Any]) -> None:
        self.entries = list(entries)
        self.invalidate()

    def configure(self, similarity_threshold: float, substring_threshold: float,
                  min_query_length: int, min_contains_length: int) -> None:
        self.similarity_threshold = float(similarity_threshold)
        self.substring_threshold = float(substring_threshold)
        self.min_query_length = max(1, int(min_query_length))
        self.min_contains_length = max(1, int(min_contains_length))
        self.invalidate()

    def invalidate(self) -> None:
        self._cache.clear()
        self._order.clear()

    # ---- 主流程 ----
    def match(self, question_text: str) -> MatchResult:
        started = time.time()
        query = normalize(question_text)

        if not query:
            return MatchResult(mode="skipped", query_length=0,
                               elapsed=time.time() - started)

        if query in self._cache:
            cached = self._cache[query]
            return MatchResult(**{**cached.__dict__, "elapsed": time.time() - started})

        if len(query) < self.min_query_length:
            # 过短文本禁止走「包含」与相似度，避免「下列说法正确的是」秒中
            result = MatchResult(mode="skipped", query_length=len(query),
                                 elapsed=time.time() - started)
            self._remember(query, result)
            return result

        result = self._match_uncached(query)
        result.elapsed = time.time() - started
        self._remember(query, result)
        return result

    def _match_uncached(self, query: str) -> MatchResult:
        q_set = frozenset(query)
        q_len = len(query)

        best_contains: Optional[Any] = None
        best_contains_len = -1
        best_sim: Optional[Any] = None
        best_sim_score = 0.0
        best_sim_text = ""
        best_sub: Optional[Any] = None
        best_sub_score = 0.0
        best_sub_text = ""
        tops: List[TopCandidate] = []

        for entry in self.entries:
            for candidate in getattr(entry, "candidates", ()):  # 惰性生成过
                norm = candidate.normalized
                if not norm:
                    continue

                # 完全一致：短路
                if norm == query:
                    return MatchResult(entry=entry, mode="exact", score=1.0,
                                       candidate=candidate.text, query_length=q_len,
                                       top=self._collect_top(tops, entry, 1.0, "exact"))

                overlap = char_overlap(q_set, candidate.char_set)
                if overlap < 0.34:
                    continue                      # 字符集几乎不相交，后续计算没意义

                # 「包含」判定：短串长度设门槛，防止「体育」命中「体育理论测试」
                shorter = min(q_len, len(norm))
                if (shorter >= self.min_contains_length
                        and (norm in query or query in norm)):
                    if len(norm) > best_contains_len:
                        best_contains = entry
                        best_contains_len = len(norm)
                    tops.append(TopCandidate(getattr(entry, "question", ""),
                                             getattr(entry, "answer", ""), 1.0, "contains"))
                    continue

                ratio_value = 0.0
                length_ratio = min(q_len, len(norm)) / max(q_len, len(norm))
                if length_ratio >= 0.5:
                    ratio_value = similarity(query, norm)
                    if ratio_value > best_sim_score:
                        best_sim, best_sim_score, best_sim_text = entry, ratio_value, candidate.text

                sub_value = 0.0
                if length_ratio >= 0.2:
                    sub_value = containment_score(query, norm)
                    if sub_value > best_sub_score:
                        best_sub, best_sub_score, best_sub_text = entry, sub_value, candidate.text

                local_best = max(ratio_value, sub_value)
                if local_best > 0.5:
                    tops.append(TopCandidate(getattr(entry, "question", ""),
                                             getattr(entry, "answer", ""),
                                             round(local_best, 4), "candidate"))

        top3 = self._collect_top(tops, None, 0, "")

        if best_contains is not None:
            return MatchResult(entry=best_contains, mode="contains", score=1.0,
                               candidate=getattr(best_contains, "question", ""),
                               query_length=q_len, top=top3)

        if best_sub is not None and best_sub_score >= self.substring_threshold:
            return MatchResult(entry=best_sub, mode="substring", score=best_sub_score,
                               candidate=best_sub_text, query_length=q_len, top=top3)

        if best_sim is not None and best_sim_score >= self.similarity_threshold:
            return MatchResult(entry=best_sim, mode="similarity", score=best_sim_score,
                               candidate=best_sim_text, query_length=q_len, top=top3)

        score = max(best_sim_score, best_sub_score)
        candidate_text = best_sub_text if best_sub_score >= best_sim_score else best_sim_text
        return MatchResult(entry=None, mode="miss", score=score,
                           candidate=candidate_text or (top3[0].question if top3 else ""),
                           query_length=q_len, top=top3)

    # ---- 内部 ----
    @staticmethod
    def _collect_top(existing: List[TopCandidate], entry: Optional[Any],
                     score: float, mode: str, limit: int = 3) -> List[TopCandidate]:
        pool = list(existing)
        if entry is not None:
            pool.append(TopCandidate(getattr(entry, "question", ""),
                                     getattr(entry, "answer", ""), score, mode))
        seen, unique = set(), []
        for item in sorted(pool, key=lambda c: c.score, reverse=True):
            key = (item.question, item.answer)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= limit:
                break
        return unique

    def _remember(self, query: str, result: MatchResult) -> None:
        if self.cache_size <= 0:
            return
        self._cache[query] = result
        self._order.append(query)
        while len(self._order) > self.cache_size:
            old = self._order.pop(0)
            self._cache.pop(old, None)
