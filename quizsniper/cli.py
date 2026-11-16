"""命令行交互模式（保留旧版操作手感，逻辑换成新内核）。

旧版把所有分支写在 ``main()`` 里，配置、截图、识别、输出互相缠绕。
现在 CLI 只负责「问与答」，真正的识别走 :class:`quizsniper.pipeline.Solver`。
"""

from __future__ import annotations

import sys
import time
from typing import List, Optional

from . import __version__
from . import capture as capture_module
from .config import PROVIDER_PRESETS, AppConfig, default_base_url
from .logbus import LogBus
from .pipeline import Solver, SolveResult

LINE = "=" * 68


def _ask(prompt: str, default: str = "") -> str:
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return raw or default


def _ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _ask(f"{prompt} ({hint}): ").lower()
    if not answer:
        return default
    return answer in ("y", "yes", "1", "true")


def _choose(prompt: str, options: List[str]) -> Optional[int]:
    for index, option in enumerate(options, 1):
        print(f"  {index}. {option}")
    raw = _ask(prompt)
    if not raw.isdigit():
        return None
    value = int(raw)
    if 1 <= value <= len(options):
        return value - 1
    return None


# ----------------------------------------------------------------------
def configure_model(cfg: AppConfig, log: LogBus) -> bool:
    from .llm import LLMClient

    print(f"\n{LINE}\n模型配置（OpenAI 兼容接口）\n{LINE}")
    names = [name for name, _ in PROVIDER_PRESETS]
    index = _choose("选择提供方序号: ", names)
    if index is None:
        print("✗ 输入无效")
        return False

    provider, url = PROVIDER_PRESETS[index]
    cfg.provider_name = provider
    if url:
        custom = _ask(f"Base URL（回车使用 {url}）: ")
        cfg.base_url = (custom or url).rstrip("/")
    else:
        while True:
            custom = _ask("输入 Base URL（例如 https://example.com/v1）: ")
            if custom:
                cfg.base_url = custom.rstrip("/")
                break
            print("✗ 不能为空")

    while True:
        key = _ask("API Key: ")
        if key:
            cfg.api_key = key
            break
        print("✗ 不能为空")

    client = LLMClient(cfg, log)
    print("\n正在拉取模型列表…")
    models, error = client.fetch_models()
    if error:
        print(f"! {error}")
        if _ask_yes("手动输入模型 ID?", True):
            while True:
                model = _ask("模型 ID: ")
                if model:
                    cfg.model = model
                    break
                print("✗ 不能为空")
        else:
            client.close()
            return False
    else:
        print(f"共 {len(models)} 个模型")
        keyword = _ask("关键词过滤（回车显示前 50 个）: ").lower()
        filtered = [m for m in models if keyword in m.lower()] if keyword else models
        if not filtered:
            print("✗ 没有匹配，改为手动输入")
            cfg.model = _ask("模型 ID: ")
        else:
            shown = filtered[:50]
            choice = _choose("选择模型序号（也可直接输入完整 ID）: ", shown)
            if choice is not None:
                cfg.model = shown[choice]
            else:
                cfg.model = _ask("模型 ID: ") or (shown[0] if shown else "")
    client.close()

    if not cfg.model:
        print("✗ 未选择模型")
        return False

    cfg.model_enabled = True
    cfg.default_background_prompt = _ask(
        "默认答题背景提示词（可留空，如：本场为体育理论考试）: ",
        cfg.default_background_prompt)
    cfg.save()
    print(f"\n✓ 配置已保存，模型：{cfg.model}")
    return True


def configure_window(solver: Solver, cfg: AppConfig) -> None:
    if not _ask_yes("设置固定窗口?", False):
        return
    windows = capture_module.list_windows()
    if not windows:
        print("✗ 没有可用窗口")
        return
    for index, info in enumerate(windows[:40], 1):
        print(f"  {index:>2}. {info.label()}")

    raw = _ask("输入序号或窗口标题关键词: ")
    target = None
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= min(len(windows), 40):
            target = windows[index - 1]
    else:
        for info in windows:
            if raw.lower() in info.title.lower():
                target = info
                break

    if target is None:
        print("✗ 未找到窗口")
        return

    cfg.window_title = target.title
    print(f"✓ 已选择：{target.title}  ({target.width}×{target.height})")

    if not _ask_yes("设置固定识别区域?", False):
        cfg.region = []
        cfg.region_mode = "none"
        cfg.save()
        return

    print(f"输入相对窗口左上角的坐标 左,上,右,下，窗口尺寸 {target.width}×{target.height}")
    print(f"例：0,120,{target.width},520")
    raw = _ask("区域: ")
    try:
        coords = [int(part.strip()) for part in raw.split(",")]
    except ValueError:
        print("✗ 格式错误")
        return
    if len(coords) != 4:
        print("✗ 需要 4 个数字")
        return
    left, top, right, bottom = coords
    if right <= left or bottom <= top or left < 0 or top < 0:
        print("✗ 区域无效")
        return
    cfg.region = coords
    cfg.region_mode = "window"
    cfg.save()
    print("✓ 区域已保存")


# ----------------------------------------------------------------------
def print_result(result: SolveResult) -> None:
    print(f"\n{'-' * 60}")
    if result.ok:
        print(f"✅ {result.source}答案：{result.answer}")
        print(f"   依据：{result.detail}")
    else:
        print(f"❌ 未命中：{result.error or result.detail}")
    if result.question:
        preview = result.question if len(result.question) <= 300 else result.question[:300] + "…"
        print(f"📝 识别题目：\n{preview}")
    if result.candidates and not result.ok:
        print("   最接近的题库题目：")
        for index, item in enumerate(result.candidates, 1):
            print(f"     {index}. [{item.score:.2f}] {item.answer} · {item.question[:48]}")
    if result.timings:
        parts = [f"{key} {value:.2f}s" for key, value in result.timings.items()]
        print("⏱  " + " · ".join(parts) + f" · 合计 {result.total:.2f}s")
    print(f"{'-' * 60}\n")


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or []
    log = LogBus(echo=True)
    cfg = AppConfig.load()
    solver = Solver(cfg, log)

    print(f"\n{LINE}")
    print(f"  QuizSniper {__version__} · 截图答题（OCR + 本地题库 + 大模型）")
    print(f"{LINE}")
    print("回车截图识别 · q 退出 · w 换窗口 · r 设区域 · s 统计\n")

    use_model = _ask_yes("启用大模型答题?", cfg.model_ready())
    if use_model:
        if cfg.model_ready() and _ask_yes(f"使用已保存配置（{cfg.model}）?", True):
            cfg.model_enabled = True
        elif not configure_model(cfg, log):
            print("! 模型未配置，将只使用题库")
            cfg.model_enabled = False
    else:
        cfg.model_enabled = False

    use_bank = _ask_yes("启用题库优先?", cfg.question_bank_enabled)
    cfg.question_bank_enabled = use_bank

    if use_bank:
        report = solver.reload_bank()
        print(f"题库：{report.summary()}")
        if report.errors:
            for error in report.errors[:5]:
                print(f"  ! {error}")
        print("正在加载离线 OCR 引擎…")
        ok, reason = solver.warm_ocr()
        if not ok:
            print(f"✗ OCR 不可用：{reason}")
        else:
            print(f"✓ OCR 就绪（{solver.ocr.warm_elapsed:.2f}s）")

    solver.apply_config(cfg)

    if not cfg.question_bank_enabled and not cfg.model_ready():
        print("\n❌ 题库与模型都不可用，退出。")
        return 1

    configure_window(solver, cfg)

    print(f"\n{LINE}\n准备就绪，看到题目后按回车。\n{LINE}")

    while True:
        try:
            command = _ask("按回车识别 (q退出/w窗口/r区域/s统计): ").lower()
        except KeyboardInterrupt:
            print("\n已中断")
            break

        if command in ("q", "quit", "exit"):
            break
        if command == "w":
            cfg.window_title = ""
            cfg.region = []
            cfg.region_mode = "none"
            configure_window(solver, cfg)
            continue
        if command == "r":
            configure_window(solver, cfg)
            continue
        if command == "s":
            stats = solver.stats.as_dict()
            print(f"\n共 {stats['total']} 次 · 题库 {stats['bank']} · AI {stats['ai']} · "
                  f"未命中 {stats['miss']} · 命中率 {stats['hit_rate']:.0f}% · "
                  f"均速 {stats['speed']:.2f}s/题\n")
            continue

        started = time.time()
        result = solver.capture_and_solve()
        print_result(result)
        if result.ok and cfg.copy_to_clipboard:
            print(f"（{time.time() - started:.2f}s）")

    stats = solver.stats.as_dict()
    print(f"\n{LINE}")
    print(f"📊 本次统计：{stats['total']} 次 · 题库 {stats['bank']} · AI {stats['ai']} · "
          f"未命中 {stats['miss']}")
    print(f"{LINE}\n再见。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
