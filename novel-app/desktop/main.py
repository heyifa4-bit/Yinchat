"""明阴全自动小说 - 电脑版兼容入口。

直接复用根目录 main.py 的完整启动流程
（启动小窗口 → 内嵌 embedding 加载 → 关闭小窗口 → 主窗口）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import main  # noqa: E402

if __name__ == "__main__":
    main()

