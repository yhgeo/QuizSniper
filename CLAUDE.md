# CLAUDE.md

本文件给在本仓库工作的助手/开发者提供架构与约定。改动代码前先读「易错点」一节。

## 项目形态

Windows 桌面答题辅助：截图 → 离线 OCR → 本地题库模糊匹配 → 未命中时调 OpenAI 兼容接口兜底。
两种入口共用同一套内核：

- `run.py` → `quizsniper/ui/app.py`（Tkinter 图形界面，主力入口）
- `answer_helper.py` → `quizsniper/cli.py`（命令行，保留旧操作手感）

`quizsniper/pipeline.py` 的 `Solver` 是唯一的业务编排点，GUI 与 CLI 都只调它，不要在界面层重写识别流程。

## 常用命令

```bash
pip install -r requirements.txt
python run.py                                  # 图形界面
python answer_helper.py                        # 命令行
python -m unittest discover -s tests -v        # 单元测试（不联网、不开窗口）
python tools/selftest.py                       # 端到端自检：合成图跑 OCR + 匹配
python -m compileall -q quizsniper run.py answer_helper.py
```

游戏化验证界面时不要长期占用桌面：起 `tk.Tk()` 后用 `root.after(ms, app.on_close)` 自动关闭。

## 模块职责

| 模块 | 职责 | 不该做的事 |
|---|---|---|
| `config.py` | `AppConfig` 数据类、路径解析、原子写入 | 不读业务状态 |
| `logbus.py` | 日志环形缓冲 + 订阅回调 | 不在日志里写 API Key |
| `capture.py` | 窗口枚举、DWM 边界、截屏、区域换算 | 不碰 PIL 图像处理逻辑 |
| `ocr.py` | RapidOCR 封装、多变体投票、行序 | 不做题库匹配 |
| `matcher.py` | 规范化、四段式匹配、缓存 | 不读文件 |
| `questionbank.py` | 加载/去重/增删改/导入导出 | 不改写只读题库文件 |
| `llm.py` | HTTP 客户端、重试、参数降级、错误分类 | 不拼业务 prompt 之外的内容 |
| `pipeline.py` | 编排 + 统计 | 不直接操作控件 |
| `hotkey.py` | RegisterHotKey 线程 | 不在回调里碰 Tk |
| `ui/*` | 只做展示与交互，耗时操作走 `TaskRunner` | 不在界面层写算法 |

## 关键约定

### 线程

Tk 不是线程安全的。任何后台线程只能把结果塞进 `queue`，由主线程的 `after` 轮询消费：

- 主窗口用 `self._events` + `_pump()`；
- 面板用 `ui/tasks.py` 的 `TaskRunner`。

**不要**在后台线程里直接 `widget.configure(...)` 或调用 `root.after`。

### 打包顺序

`pack` 里先 pack 的 `expand=True` 控件会吃掉全部剩余空间，后 pack 的兄弟控件会被裁掉。
主体 + 常驻条（状态栏、编辑区）混排时用 `grid` + `rowconfigure(weight=1)`，或先 pack 常驻条。
历史上踩过两次：状态栏整条不可见、题库编辑区按钮被裁。

### 匹配算法

- 一律 `difflib.SequenceMatcher(..., autojunk=False)`。默认的 `autojunk=True` 会在长度 > 200 时丢弃高频字符，中文长题干会被系统性低估。
- 子串相似度用 `get_matching_blocks()` 求「较短串被覆盖比例」，不要逐窗口调 `ratio()`（O(n²·m)）。
- 短于 `min_query_length` 的查询直接 `skipped`，否则「下列说法正确的是」会命中一切。
- 阈值改动要同步跑 `tests/test_core.py::MatcherTest`。

### 题库分层

`question_banks/*.json` 只读，`answer_question_bank.json` 可写。
`save_user_bank()` 只序列化 `entry.writable is True` 的条目——**不要**改成保存整个 `self.entries`，否则会覆盖用户的内置题库。

### 网络

`LLMClient` 用共享 `requests.Session`（连接池 + `Retry`）。
参数降级顺序：`temperature + max_tokens` → `max_tokens` → `max_completion_tokens`。
只在「参数不被支持」时才降级重发；401/403/429 直接返回，换参数没用。
错误分类看状态码，不要用消息子串匹配（模型名里可能带 500）。

### OCR 预处理

不要恢复硬二值化。`OcrEngine._variants()` 生成原图 / 灰度放大 / 反色三份交给引擎投票，
深色界面靠「平均亮度 < 110 → 追加反色」兜住。行序用 box 高度中位数做聚类容差。

### 配置

`AppConfig.from_dict` 必须能容忍缺字段、未知字段、类型错误（旧版本配置要能平滑升级）。
新增字段给默认值即可，不要加「必需字段」校验。

## 数据与路径

- `app_home()`：`QUIZSNIPER_HOME` 环境变量 > PyInstaller 冻结目录 > 源码上级目录。
- 运行产物（预览图、日志）不要写进包目录；`logs/`、`answer_helper_config.json` 已在 `.gitignore`。

## 提交前

1. `python -m unittest discover -s tests -v` 全绿；
2. `python -m compileall -q quizsniper run.py answer_helper.py` 无输出；
3. 界面有改动时，起窗截图确认没有裁切/错位（用 `capture.window_info(root.winfo_id())` 取窗口物理边界再 `ImageGrab.grab`）；
4. 确认没有把 `answer_helper_config.json`、`answer_question_bank.json`、真实题库提交上去。
