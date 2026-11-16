"""核心逻辑单元测试（不依赖网络、不依赖 GUI）。

跑法：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quizsniper import matcher, questionbank
from quizsniper.config import AppConfig


def make_entry(question: str, answer: str, aliases=()):
    return questionbank.BankEntry(question=question, answer=answer, aliases=list(aliases))


class NormalizeTest(unittest.TestCase):
    def test_strips_markers_and_punctuation(self):
        raw = "健康是人类实现高品质生活的必备条件， 什么可以帮助抵御现代社会的“文明病”？ 【答案】"
        self.assertEqual(matcher.normalize(raw),
                         "健康是人类实现高品质生活的必备条件什么可以帮助抵御现代社会的文明病")

    def test_fullwidth_folding(self):
        self.assertEqual(matcher.normalize("ＡＢＣ１２３"), "abc123")

    def test_strip_options(self):
        text = "以下哪项属于有氧运动？\nA. 深蹲\nB. 慢跑\nC. 卧推\nD. 硬拉"
        self.assertEqual(matcher.strip_options(text), "以下哪项属于有氧运动？")


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.entries = [
            make_entry("体育生活方式是什么", "B", ["体育生活方式"]),
            make_entry("健康是人类实现高品质生活的必备条件，什么可以帮助抵御现代社会的文明病", "B"),
            make_entry("下列哪项不属于有氧运动", "C"),
        ]
        self.engine = matcher.BankMatcher(self.entries, 0.72, 0.80, 8, 8)

    def test_exact(self):
        result = self.engine.match("体育生活方式是什么")
        self.assertTrue(result.hit)
        self.assertEqual(result.mode, "exact")
        self.assertEqual(result.answer, "B")

    def test_punctuation_noise(self):
        result = self.engine.match("体育生活方式，是什么？")
        self.assertTrue(result.hit)

    def test_short_query_is_skipped(self):
        result = self.engine.match("体育")
        self.assertFalse(result.hit)
        self.assertEqual(result.mode, "skipped")

    def test_contains_guard(self):
        # 只有 6 个字，短于 min_query_length，不能命中长题
        result = self.engine.match("文明病")
        self.assertFalse(result.hit)

    def test_missing_characters_tolerated(self):
        # OCR 漏掉「有氧」两个字，子串覆盖率仍应过阈值
        result = self.engine.match("下列哪项不属于运动")
        self.assertTrue(result.hit)
        self.assertEqual(result.answer, "C")

    def test_long_repeated_text_not_penalised_by_autojunk(self):
        # 长题干里「的」重复出现，旧实现 autojunk=True 会把常用字当噪声丢掉
        question = "的" * 120 + "体育理论考试中关于运动处方的核心原则是什么" + "的" * 120
        entry = make_entry(question, "A")
        engine = matcher.BankMatcher([entry], 0.72, 0.80, 8, 8)
        query = "体育理论考试中关于运动处方的核心原则是什么"
        result = engine.match(query)
        self.assertTrue(result.hit)
        self.assertGreater(result.score, 0.8)

    def test_miss_returns_candidates(self):
        result = self.engine.match("完全不相关的天文物理题目内容")
        self.assertFalse(result.hit)
        self.assertEqual(result.mode, "miss")

    def test_cache_invalidation(self):
        self.engine.match("体育生活方式是什么")
        self.engine.set_entries([])
        self.assertFalse(self.engine.match("体育生活方式是什么").hit)


class QuestionBankTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bank_dir = self.root / "question_banks"
        self.bank_dir.mkdir()
        (self.bank_dir / "readonly.json").write_text(json.dumps({
            "entries": [
                {"question": "只读题目一", "answer": "A"},
                {"question": "只读题目二", "answer": "B"},
            ]
        }, ensure_ascii=False), encoding="utf-8")
        self.user_file = self.root / "answer_question_bank.json"
        self.bank = questionbank.QuestionBank(self.bank_dir, self.user_file)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_and_dedupe(self):
        report = self.bank.load()
        self.assertEqual(len(report.entries), 2)
        self.assertEqual(report.duplicates, 0)
        self.assertTrue(any("readonly.json" in name for name in report.files))

    def test_add_persists_to_user_file(self):
        self.bank.load()
        self.bank.add("新题目", "C")
        data = json.loads(self.user_file.read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["answer"], "C")

    def test_readonly_entry_cannot_be_deleted(self):
        self.bank.load()
        readonly = next(e for e in self.bank.entries if not e.writable)
        with self.assertRaises(ValueError):
            self.bank.delete(readonly.entry_id)

    def test_add_duplicate_updates_answer(self):
        self.bank.load()
        entry = self.bank.add("新题目", "C")
        again = self.bank.add("新题目", "D")
        self.assertEqual(entry.entry_id, again.entry_id)
        self.assertEqual(again.answer, "D")
        self.assertEqual(sum(1 for e in self.bank.entries if e.writable), 1)

    def test_import_skips_duplicates(self):
        self.bank.load()
        extra = self.root / "extra.json"
        extra.write_text(json.dumps({"entries": [
            {"question": "只读题目一", "answer": "A"},
            {"question": "导入题目", "answer": "D"},
        ]}, ensure_ascii=False), encoding="utf-8")
        added, skipped, error = self.bank.import_file(extra)
        self.assertEqual((added, skipped, error), (1, 1, ""))


class ConfigTest(unittest.TestCase):
    def test_roundtrip(self):
        cfg = AppConfig(provider_name="DeepSeek", api_key="sk-123", model="deepseek-chat",
                        similarity_threshold=0.66, region=[1, 2, 300, 400],
                        region_mode="window")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            cfg.save(path)
            loaded = AppConfig.load(path)
        self.assertEqual(loaded.provider_name, "DeepSeek")
        self.assertAlmostEqual(loaded.similarity_threshold, 0.66)
        self.assertEqual(loaded.region, [1, 2, 300, 400])
        self.assertEqual(loaded.region_mode, "window")

    def test_unknown_fields_ignored(self):
        cfg = AppConfig.from_dict({"provider_name": "OpenAI", "legacy_field": 1,
                                   "model_enabled": "yes"})
        self.assertEqual(cfg.provider_name, "OpenAI")
        self.assertTrue(cfg.model_enabled)

    def test_broken_region_reset(self):
        cfg = AppConfig.from_dict({"region": [1, 2, 3]})
        self.assertEqual(cfg.region, [])


if __name__ == "__main__":
    unittest.main()
