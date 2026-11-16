"""安装自检：不联网、不截屏，用合成图跑一遍 OCR + 题库匹配。

用法：
    python tools/selftest.py

成功输出两行 ``[OK]``，退出码 0；任一环节失败退出码 1。
用来确认 rapidocr / numpy / Pillow 装好、字体可用、匹配阈值合理。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont

from quizsniper.matcher import BankMatcher
from quizsniper.ocr import OcrEngine
from quizsniper.questionbank import BankEntry

QUESTION = "以下哪项属于典型的有氧运动方式"
OPTIONS = ["A. 100 米冲刺", "B. 慢跑 30 分钟", "C. 引体向上", "D. 举重深蹲"]
BANK = [
    ("以下哪项属于典型的有氧运动方式", "B"),
    ("体育课上测量肺活量主要反映哪项素质", "A"),
    ("运动损伤后 24 小时内应采取的处理方式是", "C"),
]

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render(dark: bool = False) -> Image.Image:
    width, height = 860, 420
    bg = (18, 20, 26) if dark else (250, 250, 252)
    fg = (235, 238, 245) if dark else (24, 26, 32)
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    title_font = load_font(30)
    body_font = load_font(26)

    draw.text((40, 40), QUESTION, font=title_font, fill=fg)
    y = 120
    for line in OPTIONS:
        draw.text((60, y), line, font=body_font, fill=fg)
        y += 62
    if dark:
        draw.rectangle([0, 0, width - 1, height - 1], outline=(40, 46, 58))
    return image


def main() -> int:
    engine = OcrEngine(scale=2.0, binarize=False, auto_invert=True)
    print("正在加载 OCR 引擎…")
    started = time.time()
    ok, reason = engine.warmup()
    if not ok:
        print(f"[FAIL] OCR 初始化失败：{reason}")
        return 1
    print(f"[OK] OCR 引擎就绪（{time.time() - started:.2f}s）")

    entries = [BankEntry(question=q, answer=a) for q, a in BANK]
    matcher = BankMatcher(entries, 0.72, 0.80, 8, 8)

    failures = 0
    for dark in (False, True):
        image = render(dark=dark)
        result = engine.recognize(image)
        label = "深色底" if dark else "浅色底"
        if not result.ok:
            print(f"[FAIL] {label}：OCR 没有识别到文字（变体 {result.variant or '无'}）")
            failures += 1
            continue
        match = matcher.match(result.text)
        preview = result.text.replace("\n", " / ")[:70]
        if match.hit:
            print(f"[OK] {label}：变体 {result.variant}，置信度 {result.mean_score:.2f}，"
                  f"识别「{preview}」→ 答案 {match.answer}（{match.label()} {match.score:.2f}）")
        else:
            print(f"[WARN] {label}：识别成功但未命中题库（最高 {match.score:.2f}）")
            print(f"       识别文本：{preview}")
            failures += 1
        if image.size[0] > 0:
            out = ROOT / "logs" / f"selftest-{'dark' if dark else 'light'}.png"
            out.parent.mkdir(exist_ok=True)
            image.save(out)

    if failures:
        print(f"\n{failures} 项未通过，检查 OCR 依赖或匹配阈值。")
        return 1
    print("\n自检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
