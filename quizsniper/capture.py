"""Windows 截屏与窗口枚举。

相比旧实现的修正：
* 用 ``DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`` 拿可见边界，
  避免 Win10/11 阴影边框导致的 7~16px 偏移（旧版用 GetWindowRect 会多截一圈透明边）；
* 枚举窗口不再硬截断 20 个，返回完整列表由 UI 过滤/分页；
* 多显示器时统一用 ``all_screens=True``，副屏窗口不再全黑；
* 窗口最小化判定放在这里，避免下游反复判断。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageGrab

BBox = Tuple[int, int, int, int]

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:  # pragma: no cover - 极老系统
    dwmapi = None

DWMWA_EXTENDED_FRAME_BOUNDS = 9
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SW_RESTORE = 9

_dpi_done = False


def enable_dpi_awareness() -> None:
    """进程级 DPI 感知：让所有坐标都是物理像素。必须在建窗口前调用。"""
    global _dpi_done
    if _dpi_done:
        return
    _dpi_done = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    rect: BBox

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    def label(self) -> str:
        title = self.title if len(self.title) <= 60 else self.title[:57] + "..."
        return f"{title}  ·  {self.width}×{self.height}"

    def __str__(self) -> str:  # 供 Combobox 直接显示
        return self.label()


# ----------------------------------------------------------------------
# 窗口枚举
# ----------------------------------------------------------------------
def _window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def _window_rect(hwnd: int) -> Optional[BBox]:
    if dwmapi is not None:
        rect = wintypes.RECT()
        try:
            status = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if status == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            pass
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(wintypes.HWND(hwnd)))


def list_windows(min_width: int = 160, min_height: int = 120,
                 include_offscreen: bool = False) -> List[WindowInfo]:
    """返回所有可见、有标题、非工具窗口的顶层窗口（Z 序从上到下）。"""
    result: List[WindowInfo] = []
    own_pid = ctypes.windll.kernel32.GetCurrentProcessId()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_text(hwnd)
        if not title:
            return True
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW or ex_style & WS_EX_NOACTIVATE:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        rect = _window_rect(hwnd)
        if not rect:
            return True
        left, top, right, bottom = rect
        if right - left < min_width or bottom - top < min_height:
            return True
        if not include_offscreen and is_minimized(hwnd):
            return True
        result.append(WindowInfo(int(hwnd), title, rect))
        return True

    user32.EnumWindows(_callback, 0)

    # 标题去重，保留最上层那个
    seen = set()
    unique: List[WindowInfo] = []
    for info in result:
        key = info.title.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(info)
    return unique


def find_window(title_keyword: str) -> Optional[WindowInfo]:
    keyword = (title_keyword or "").strip().lower()
    if not keyword:
        return None
    windows = list_windows()
    for info in windows:
        if info.title.lower() == keyword:
            return info
    for info in windows:
        if keyword in info.title.lower():
            return info
    return None


def window_info(hwnd: int) -> Optional[WindowInfo]:
    rect = _window_rect(hwnd)
    if not rect:
        return None
    return WindowInfo(int(hwnd), _window_text(hwnd), rect)


def focus_window(hwnd: int) -> None:
    try:
        if is_minimized(hwnd):
            user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception:
        pass


# ----------------------------------------------------------------------
# 截屏
# ----------------------------------------------------------------------
def virtual_screen_rect() -> BBox:
    metrics = (76, 77, 78, 79)  # SM_XVIRTUALSCREEN / Y / CX / CY
    values = [user32.GetSystemMetrics(m) for m in metrics]
    return (values[0], values[1], values[0] + values[2], values[1] + values[3])


def grab(bbox: Optional[Sequence[int]] = None, all_screens: bool = True) -> Image.Image:
    """截图。bbox 为物理像素坐标。"""
    if bbox is not None:
        left, top, right, bottom = [int(v) for v in bbox]
        if right <= left or bottom <= top:
            raise ValueError(f"无效截图区域: {tuple(bbox)}")
        return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=all_screens)
    return ImageGrab.grab(all_screens=all_screens)


def clamp_bbox(bbox: Sequence[int]) -> Optional[BBox]:
    """把 bbox 裁剪到虚拟屏幕范围内，完全越界返回 None。"""
    left, top, right, bottom = [int(v) for v in bbox]
    sx, sy, sx2, sy2 = virtual_screen_rect()
    left, top = max(left, sx), max(top, sy)
    right, bottom = min(right, sx2), min(bottom, sy2)
    if right - left < 8 or bottom - top < 8:
        return None
    return (left, top, right, bottom)


def resolve_bbox(window: Optional[WindowInfo], region_mode: str,
                 region: Sequence[int]) -> Tuple[Optional[BBox], str]:
    """根据窗口 + 区域配置算出实际截图区域。

    region_mode:
      ``window`` —— region 是相对窗口左上角的坐标，窗口移动后跟随；
      ``screen`` —— region 是绝对屏幕坐标；
      ``none``   —— 有窗口就截整个窗口，否则全屏。
    """
    has_region = bool(region) and len(region) == 4

    if region_mode == "window" and has_region:
        if window is None:
            return None, "区域模式为「跟随窗口」，但未选择窗口"
        wx, wy = window.rect[0], window.rect[1]
        bbox = (wx + int(region[0]), wy + int(region[1]),
                wx + int(region[2]), wy + int(region[3]))
        return clamp_bbox(bbox), "区域超出屏幕范围，可能窗口已移出屏幕"

    if region_mode == "screen" and has_region:
        return clamp_bbox([int(v) for v in region]), "区域超出屏幕范围"

    if window is not None:
        if is_minimized(window.hwnd):
            return None, "目标窗口已最小化，请先还原"
        fresh = window_info(window.hwnd)
        if fresh is None:
            return None, "无法读取窗口位置，窗口可能已关闭"
        return clamp_bbox(fresh.rect), "无法定位窗口"

    return clamp_bbox(virtual_screen_rect()), "无法获取屏幕范围"


def capture(window: Optional[WindowInfo], region_mode: str,
            region: Sequence[int], all_screens: bool = True) -> Tuple[Optional[Image.Image], str]:
    """返回 (图像, 错误信息)。成功时错误信息为空串。"""
    bbox, error = resolve_bbox(window, region_mode, region)
    if bbox is None:
        return None, error or "截图区域无效"
    try:
        return grab(bbox, all_screens=all_screens), ""
    except Exception as exc:  # 屏幕锁定、权限、显卡驱动异常
        return None, f"截图失败: {exc}"
