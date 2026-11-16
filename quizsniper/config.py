"""配置模型、路径解析与读写。

设计要点：
1. 路径统一由 :func:`app_home` 推导，兼容源码运行与 PyInstaller 冻结运行；
2. 读取旧版 ``answer_helper_config.json`` 时缺字段不报错，用默认值补齐；
3. 写入采用临时文件 + 原子替换，避免半截文件导致下次启动配置丢失。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_NAME = "QuizSniper"
CONFIG_FILENAME = "answer_helper_config.json"

#: (显示名, 默认 Base URL)。None 表示必须手填。
PROVIDER_PRESETS: List[tuple] = [
    ("OpenAI", "https://api.openai.com/v1"),
    ("阿里云百炼 (DashScope 兼容)", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("SiliconFlow", "https://api.siliconflow.cn/v1"),
    ("月之暗面 Moonshot", "https://api.moonshot.cn/v1"),
    ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4"),
    ("自定义 OpenAI 兼容接口", None),
]

PROVIDER_NAMES = [name for name, _ in PROVIDER_PRESETS]


def app_home() -> Path:
    """项目/程序根目录。"""
    override = os.environ.get("QUIZSNIPER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_base_url(provider_name: str) -> Optional[str]:
    for name, url in PROVIDER_PRESETS:
        if name == provider_name:
            return url
    return None


@dataclass
class AppConfig:
    """全部可持久化配置。字段名保持与旧版 JSON 兼容。"""

    # ---- 模型 ----
    provider_name: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    model_enabled: bool = False
    default_background_prompt: str = ""

    # ---- 题库 ----
    question_bank_enabled: bool = True
    bank_first: bool = True
    similarity_threshold: float = 0.72
    substring_threshold: float = 0.80
    min_query_length: int = 8
    min_contains_length: int = 8

    # ---- 截图 ----
    window_title: str = ""
    region_mode: str = "none"          # none | window | screen
    region: List[int] = field(default_factory=list)   # [left, top, right, bottom]
    all_screens: bool = True

    # ---- 识别 ----
    ocr_scale: float = 2.0
    ocr_binarize: bool = False
    ocr_auto_invert: bool = True

    # ---- 请求 ----
    request_timeout: int = 30
    max_tokens: int = 256
    temperature: float = 0.1

    # ---- 交互 ----
    hotkey: str = "F8"
    auto_interval: float = 2.0
    copy_to_clipboard: bool = False

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path) if path else config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".cfg-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return target

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        target = Path(path) if path else config_path()
        cfg = cls()
        if not target.exists():
            return cfg
        try:
            with target.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            return cfg
        if not isinstance(raw, dict):
            return cfg
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AppConfig":
        cfg = cls()
        known = {f.name for f in fields(cls)}
        for key, value in raw.items():
            if key not in known:
                continue                      # 忽略旧版/未知字段，不抛异常
            current = getattr(cfg, key)
            try:
                if isinstance(current, bool):
                    setattr(cfg, key, _as_bool(value))
                elif isinstance(current, int) and not isinstance(current, bool):
                    setattr(cfg, key, int(value))
                elif isinstance(current, float):
                    setattr(cfg, key, float(value))
                elif isinstance(current, list):
                    setattr(cfg, key, list(value) if isinstance(value, (list, tuple)) else [])
                else:
                    setattr(cfg, key, "" if value is None else str(value))
            except (TypeError, ValueError):
                continue
        # 旧版可能没有 base_url 但选了提供方
        if not cfg.base_url and cfg.provider_name:
            cfg.base_url = default_base_url(cfg.provider_name) or ""
        cfg.base_url = cfg.base_url.strip().rstrip("/")
        if cfg.region and len(cfg.region) != 4:
            cfg.region = []
        if cfg.region_mode not in ("none", "window", "screen"):
            cfg.region_mode = "none"
        return cfg

    # ------------------------------------------------------------------
    def model_ready(self) -> bool:
        return bool(self.model_enabled and self.provider_name and self.api_key
                    and self.base_url and self.model)

    def masked_key(self) -> str:
        key = self.api_key or ""
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}{'*' * 8}{key[-4:]}"

    def copy(self) -> "AppConfig":
        return AppConfig.from_dict(self.to_dict())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


# ----------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------
def config_path() -> Path:
    return app_home() / CONFIG_FILENAME


def bank_file_path() -> Path:
    return app_home() / "answer_question_bank.json"


def bank_dir_path() -> Path:
    return app_home() / "question_banks"


def log_dir_path() -> Path:
    return app_home() / "logs"


def asset_dir() -> Path:
    return app_home() / "assets"
