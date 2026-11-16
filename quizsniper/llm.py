"""OpenAI 兼容接口客户端。

相比旧实现的改进：
* 复用 ``requests.Session``（连接池 + 自动重试），每题省掉一次 TLS 握手；
* 参数降级：新模型（o 系 / gpt-5 系）拒绝 ``temperature``、要求 ``max_completion_tokens``，
  旧代码直接 400 报错，这里自动换参数重发；
* 错误按状态码分类（auth / rate / server / network / client），不再用「消息里是否含 500」
  这种子串判断——模型名里带数字会被误判；
* 上送前把图缩到 1600px 内，避免 base64 体积过大导致超时。
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import AppConfig
from .logbus import LogBus

ANSWER_PROMPT = (
    "请识别图片中的题目和选项，并直接给出正确答案。"
    "只需要回答选项的具体内容，不要说 A、B、C、D 这些字母，也不要解释。"
)
TEXT_ANSWER_PROMPT = (
    "请根据 OCR 识别出的题目和选项文本，直接给出正确答案。"
    "只需要回答选项的具体内容，不要说 A、B、C、D 这些字母，也不要解释。"
)
EXTRACT_PROMPT = (
    "请把图片中的题目与选项尽量逐字转写成纯文本，不要回答题目。"
    "不要改写，不要概括，不要补充推测。看不清的字保留原样，实在无法辨认用？代替。"
    "严格按此格式输出：\n题目：<题干全文>\n选项A：<内容>\n选项B：<内容>\n"
    "选项C：<内容>\n选项D：<内容>"
)

IMAGE_MAX_WIDTH = 1600
NETWORK_HINTS = (
    "connection", "connect", "timed out", "timeout", "name or service",
    "temporary failure", "max retries", "proxy", "ssl", "refused",
    "unreachable", "dns", "network", "reset by peer", "broken pipe",
)
PARAM_HINTS = ("max_tokens", "temperature", "unsupported", "unknown parameter",
               "invalid parameter", "unrecognized", "not supported", "extra fields")


@dataclass
class ChatResult:
    text: str = ""
    error: str = ""
    status: int = 0
    category: str = ""          # ok | config | auth | rate | server | network | client | empty
    elapsed: float = 0.0
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


def classify(status: int, message: str) -> str:
    text = (message or "").lower()
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate"
    if status in (400, 404, 422):
        return "client"
    if status >= 500:
        return "server"
    if status == 0 and any(hint in text for hint in NETWORK_HINTS):
        return "network"
    return "client" if status else "network"


class LLMClient:
    def __init__(self, cfg: AppConfig, log: Optional[LogBus] = None):
        self.cfg = cfg
        self.log = log
        self._session: Optional[requests.Session] = None

    # ------------------------------------------------------------------
    def update_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def is_ready(self) -> bool:
        return self.cfg.model_ready()

    def _get_session(self) -> requests.Session:
        if self._session is None:
            session = requests.Session()
            retry = Retry(
                total=2,
                connect=2,
                read=1,
                backoff_factor=0.4,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset(["GET", "POST"]),
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            self._session = session
        return self._session

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None

    # ------------------------------------------------------------------
    def build_url(self, path: str) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"

    def headers(self, with_json: bool = False) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        if with_json:
            headers["Content-Type"] = "application/json"
        if "openrouter" in (self.cfg.base_url or "").lower():
            headers["HTTP-Referer"] = "https://localhost"
            headers["X-Title"] = "QuizSniper"
        return headers

    # ------------------------------------------------------------------
    def chat(self, messages: Sequence[Dict[str, Any]], max_tokens: Optional[int] = None,
             temperature: Optional[float] = None) -> ChatResult:
        if not self.is_ready():
            return ChatResult(error="模型未配置或未启用", category="config")

        max_tokens = int(max_tokens or self.cfg.max_tokens)
        temperature = float(self.cfg.temperature if temperature is None else temperature)

        payloads = [
            {"model": self.cfg.model, "messages": list(messages),
             "temperature": temperature, "max_tokens": max_tokens},
            {"model": self.cfg.model, "messages": list(messages), "max_tokens": max_tokens},
            {"model": self.cfg.model, "messages": list(messages),
             "max_completion_tokens": max_tokens},
        ]

        started = time.time()
        last = ChatResult(category="client")
        for index, payload in enumerate(payloads, 1):
            try:
                response = self._get_session().post(
                    self.build_url("chat/completions"),
                    headers=self.headers(with_json=True),
                    json=payload,
                    timeout=self.cfg.request_timeout,
                )
            except Exception as exc:
                last = ChatResult(error=f"请求异常: {exc}", status=0,
                                  category="network", elapsed=time.time() - started,
                                  attempts=index)
                break

            status = response.status_code
            if status == 200:
                text = self._extract_text(self._safe_json(response))
                if text:
                    return ChatResult(text=text, status=200, category="ok",
                                      elapsed=time.time() - started, attempts=index)
                return ChatResult(error="模型返回为空", status=200, category="empty",
                                  elapsed=time.time() - started, attempts=index)

            body = self._error_text(response)
            category = classify(status, body)
            last = ChatResult(error=f"HTTP {status}: {body}", status=status,
                              category=category, elapsed=time.time() - started,
                              attempts=index)

            # 只在「参数不被支持」时降级重试，鉴权/限流等直接返回
            if category == "client" and index < len(payloads):
                if any(hint in body.lower() for hint in PARAM_HINTS):
                    continue
            break

        if self.log and last.error:
            self.log.warn(f"模型请求失败（{last.category}）：{last.error[:200]}")
        return last

    # ------------------------------------------------------------------
    def fetch_models(self) -> Tuple[List[str], str]:
        if not self.cfg.base_url or not self.cfg.api_key:
            return [], "Base URL 或 API Key 为空"
        try:
            response = self._get_session().get(
                self.build_url("models"), headers=self.headers(),
                timeout=min(20, self.cfg.request_timeout),
            )
        except Exception as exc:
            return [], f"请求模型列表失败: {exc}"
        if response.status_code != 200:
            return [], f"获取模型列表失败 ({response.status_code}): {self._error_text(response)}"
        data = self._safe_json(response)
        items = data.get("data") if isinstance(data, dict) else None
        models = [str(item.get("id", "")).strip() for item in (items or [])
                  if isinstance(item, dict) and item.get("id")]
        models = list(dict.fromkeys(models))
        if not models:
            return [], "模型列表为空，接口可能不支持 /models，可手动填写模型 ID"
        return models, ""

    # ------------------------------------------------------------------
    def answer_from_text(self, question: str, background: str = "") -> ChatResult:
        prompt = TEXT_ANSWER_PROMPT
        if background.strip():
            prompt = f"{prompt}\n\n补充答题背景信息：\n{background.strip()}"
        messages = [{"role": "user",
                     "content": f"{prompt}\n\nOCR 识别文本：\n{question}"}]
        return self.chat(messages, max_tokens=self.cfg.max_tokens)

    def answer_from_image(self, image, background: str = "") -> ChatResult:
        prompt = ANSWER_PROMPT
        if background.strip():
            prompt = f"{prompt}\n\n补充答题背景信息：\n{background.strip()}"
        return self.chat(self._image_messages(image, prompt), max_tokens=self.cfg.max_tokens)

    def extract_from_image(self, image) -> ChatResult:
        return self.chat(self._image_messages(image, EXTRACT_PROMPT), max_tokens=512)

    # ------------------------------------------------------------------
    def _image_messages(self, image, prompt: str) -> List[Dict[str, Any]]:
        return [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{self._encode(image)}"}},
            ],
        }]

    @staticmethod
    def _encode(image) -> str:
        if image is None:
            return ""
        prepared = image
        width, height = image.size
        if width > IMAGE_MAX_WIDTH:
            ratio = IMAGE_MAX_WIDTH / float(width)
            prepared = image.resize((IMAGE_MAX_WIDTH, int(height * ratio)))
        buffer = BytesIO()
        prepared.convert("RGB").save(buffer, format="PNG", optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    @staticmethod
    def _safe_json(response: requests.Response) -> Dict[str, Any]:
        try:
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}
        except Exception:
            return {}

    @staticmethod
    def _error_text(response: requests.Response) -> str:
        try:
            data = response.json()
        except Exception:
            return (response.text or "").strip()[:400] or "无响应内容"
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)[:400]
            if isinstance(error, str):
                return error[:400]
            if data.get("message"):
                return str(data["message"])[:400]
        return str(data)[:400]

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                        elif isinstance(text, dict) and isinstance(text.get("value"), str):
                            parts.append(text["value"])
                return "\n".join(p.strip() for p in parts if p and p.strip())
        output_text = data.get("output_text")
        if isinstance(output_text, str):
            return output_text.strip()
        return ""
