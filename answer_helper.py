"""命令行入口（兼容旧用法：``python answer_helper.py``）。

v2 起实现拆到 ``quizsniper`` 包，本文件只做转发，保证旧命令与操作习惯不变。
推荐直接用图形界面：``python run.py``。
"""

from __future__ import annotations

import sys

from quizsniper.cli import main

if __name__ == "__main__":
    sys.exit(main())
