"""离线 OCR 封装（RapidOCR / onnxruntime）。

旧实现在识别前做了一次 ``point(255 if p>180 else 0)`` 的硬二值化，这会把
抗锯齿文字边缘直接砍掉，暗色主题（白字黑底）更是整张图变全黑、识别为空。
这里改成「多假设投票」：

1. 原图直接送识别（RapidOCR 自带方向分类与增强）；
2. 灰度 + 放大 + 自动对比度（不二值化）；
3. 检测到暗底时追加反色版本；

取「文本行数最多、平均置信度最高」的一份结果，避免单一预处理把整题打死。
同时兼容 RapidOCR 1.x 的 ``(result, elapse)`` 与 2.x 的对象式返回。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

try:  # 可选依赖，缺一个就降级而不是崩
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover
    Image = None
    ImageOps = None

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:  # pragma: no cover
    try:
        from rapidocr import RapidOCR  # 2.x 包名
    except Exception:
        RapidOCR = None


@dataclass
class OcrLine:
    text: str
    score: float
    y: float
    x: float
    h: float = 0.0


@dataclass
class OcrResult:
    text: str = ""
    lines: List[OcrLine] = field(default_factory=list)
    elapsed: float = 0.0
    variant: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())

    @property
    def mean_score(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.score for line in self.lines) / len(self.lines)


class OcrEngine:
    """线程安全的 RapidOCR 包装。首次 ``warmup`` 会下载/加载模型，约 1~3 秒。"""

    def __init__(self, scale: float = 2.0, binarize: bool = False,
                 auto_invert: bool = True):
        self.scale = max(1.0, min(4.0, float(scale)))
        self.binarize = bool(binarize)
        self.auto_invert = bool(auto_invert)
        self._engine: Any = None
        self._lock = threading.RLock()
        self._ready = False
        self._reason = "未初始化"
        self._warm_elapsed = 0.0

    # ------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def warm_elapsed(self) -> float:
        return self._warm_elapsed

    def warmup(self) -> Tuple[bool, str]:
        with self._lock:
            if self._ready:
                return True, ""
            if RapidOCR is None:
                self._reason = "未安装 rapidocr-onnxruntime"
                return False, self._reason
            if np is None:
                self._reason = "未安装 numpy"
                return False, self._reason
            started = time.time()
            try:
                self._engine = RapidOCR()
            except Exception as exc:
                self._engine = None
                self._ready = False
                self._reason = f"OCR 引擎初始化失败: {exc}"
                return False, self._reason
            self._warm_elapsed = time.time() - started
            self._ready = True
            self._reason = ""
            return True, ""

    # ------------------------------------------------------------------
    def _variants(self, image: "Image.Image") -> List[Tuple[str, Any]]:
        """生成待识别的图像变体（ndarray）。"""
        variants: List[Tuple[str, Any]] = []
        rgb = image.convert("RGB")

        if np is not None:
            arr = np.array(rgb)
            variants.append(("原图", arr[:, :, ::-1].copy()))  # RGB -> BGR

        if ImageOps is None or np is None:
            return variants

        gray = ImageOps.grayscale(rgb)
        size = (max(1, int(gray.width * self.scale)), max(1, int(gray.height * self.scale)))
        if size != gray.size:
            gray = gray.resize(size, Image.LANCZOS)
        gray = ImageOps.autocontrast(gray)
        if self.binarize:
            gray = gray.point(lambda p: 255 if p > 180 else 0)

        mean = _image_mean(gray)
        is_dark = mean < 110

        processed = gray
        if self.auto_invert and is_dark:
            processed = ImageOps.invert(gray)

        variants.append((f"灰度×{self.scale:g}", _to_bgr(processed)))

        if self.auto_invert and is_dark:
            variants.append(("反色", _to_bgr(gray)))

        return variants

    # ------------------------------------------------------------------
    def _run_engine(self, array: Any) -> Any:
        engine = self._engine
        if engine is None:
            return None
        try:
            return engine(array)
        except TypeError:  # 某些版本要求 (array, use_det=...)
            return engine(array, use_det=True, use_cls=True, use_rec=True)

    @staticmethod
    def _parse_output(output: Any) -> List[OcrLine]:
        """把各家版本的返回统一成 OcrLine 列表。"""
        # RapidOCR 2.x: RapidOCROutput(txts, scores, boxes)
        if hasattr(output, "txts") and hasattr(output, "scores"):
            txts = list(getattr(output, "txts") or [])
            scores = list(getattr(output, "scores") or [])
            boxes = list(getattr(output, "boxes") or [])
            lines: List[OcrLine] = []
            for idx, text in enumerate(txts):
                score = float(scores[idx]) if idx < len(scores) else 0.0
                y = x = h = 0.0
                if idx < len(boxes) and boxes[idx] is not None:
                    y, x, h = _box_center(boxes[idx])
                lines.append(OcrLine(str(text), score, y, x, h))
            return lines

        # 1.x: (list[[box, text, score]], elapse)
        if isinstance(output, tuple):
            output = output[0] if output else None

        lines = []
        for item in output or []:
            box = text = None
            score = 0.0
            if isinstance(item, dict):
                text = item.get("text") or item.get("txt")
                score = float(item.get("score") or item.get("confidence") or 0.0)
                box = item.get("box") or item.get("boxes")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                box, text = item[0], item[1]
                if len(item) >= 3:
                    try:
                        score = float(item[2])
                    except (TypeError, ValueError):
                        score = 0.0
            if not text:
                continue
            y, x, h = _box_center(box)
            lines.append(OcrLine(str(text), score, y, x, h))
        return lines

    def recognize(self, image: "Image.Image") -> OcrResult:
        if Image is None:
            return OcrResult(text="", variant="", elapsed=0.0)
        if not self._ready:
            ok, reason = self.warmup()
            if not ok:
                return OcrResult(text="")

        started = time.time()
        best_lines: List[OcrLine] = []
        best_name = ""
        best_rank = (-1, 0.0)

        with self._lock:
            for name, array in self._variants(image):
                try:
                    raw = self._run_engine(array)
                except Exception:
                    continue
                lines = self._parse_output(raw)
                if not lines:
                    continue
                score = sum(line.score for line in lines) / len(lines)
                rank = (len(lines), score)
                if rank > best_rank:
                    best_rank = rank
                    best_lines = lines
                    best_name = name

        elapsed = time.time() - started
        if not best_lines:
            return OcrResult(text="", lines=[], elapsed=elapsed, variant="")

        ordered = _sort_lines(best_lines)
        text = "\n".join(line.text.strip() for line in ordered if line.text.strip())
        return OcrResult(text=text.strip(), lines=ordered, elapsed=elapsed, variant=best_name)


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
def _to_bgr(pil_image: "Image.Image") -> Any:
    if np is None:
        return pil_image
    arr = np.array(pil_image.convert("RGB"))
    return arr[:, :, ::-1].copy()


def _image_mean(pil_image: "Image.Image") -> float:
    try:
        hist = pil_image.convert("L").histogram()
        total = sum(hist)
        if total == 0:
            return 255.0
        return sum(i * count for i, count in enumerate(hist)) / total
    except Exception:
        return 255.0


def _box_center(box: Any) -> Tuple[float, float, float]:
    """返回 (y_center, x_center, height)，拿不到就返回 (0,0,0)。"""
    if box is None:
        return 0.0, 0.0, 0.0
    try:
        if np is not None and isinstance(box, np.ndarray):
            box = box.tolist()
        pts = [pt for pt in box if isinstance(pt, (list, tuple)) and len(pt) >= 2]
        if pts:
            ys = [float(pt[1]) for pt in pts]
            xs = [float(pt[0]) for pt in pts]
            return sum(ys) / len(ys), sum(xs) / len(xs), max(ys) - min(ys)
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _sort_lines(lines: Sequence[OcrLine]) -> List[OcrLine]:
    """按行聚类排序：同一行内按 x 排，行间按 y 排。

    容差取「行高中位数 × 0.55」——文字行的竖直中心相差不到半行高就算同一行，
    两栏布局、行内图标插字都能正确合并，也不会像固定阈值那样在大字号下拆行。
    """
    items = [line for line in lines if line.text.strip()]
    if not items:
        return []
    if all(line.y == 0.0 for line in items):
        return list(items)

    heights = sorted(line.h for line in items if line.h > 0)
    median_h = heights[len(heights) // 2] if heights else 12.0
    tolerance = max(6.0, median_h * 0.55)

    buckets: List[List[OcrLine]] = []
    for line in sorted(items, key=lambda item: item.y):
        if buckets and abs(line.y - buckets[-1][0].y) <= tolerance:
            buckets[-1].append(line)
        else:
            buckets.append([line])

    ordered: List[OcrLine] = []
    for bucket in buckets:
        ordered.extend(sorted(bucket, key=lambda item: item.x))
    return ordered
