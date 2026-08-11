"""明阴全自动小说 - 软件主入口（单 exe 内嵌 embedding）。

流程：启动"启动中"小窗口 → 后台加载 embedding 模型 → 关闭小窗口 → 打开主窗口。
用法：
    python main.py              # 电脑桌面版
    python main.py --web        # 仅启动服务并在浏览器打开
    python main.py --port 8000  # 指定端口
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def resolve_data_root() -> Path:
    env = os.environ.get("NOVEL_DATA")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return base / "data"


def resolve_webui_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "_internal" / "webui"
    return Path(__file__).resolve().parent / "webui"


def resolve_plugins_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "_internal" / "plugins"
    return Path(__file__).resolve().parent / "plugins"


def _start_server(app, port: int) -> None:
    import uvicorn

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except Exception:
        import traceback

        try:
            with open(
                resolve_data_root() / "_server_error.log",
                "w",
                encoding="utf-8",
            ) as f:
                f.write(traceback.format_exc())
        except Exception:
            pass


def _wait_ready(port: int, timeout: float = 10.0) -> None:
    waited = 0.0
    while waited < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
            waited += 0.2


def _ensure_console_streams() -> None:
    """无控制台（exe）环境下 sys.stdout/stderr 可能为 None，uvicorn 会崩溃。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        root = resolve_data_root()
        root.mkdir(parents=True, exist_ok=True)
        f = open(root / "runtime.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f
    except Exception:
        import io

        if sys.stdout is None:
            sys.stdout = io.StringIO()
        if sys.stderr is None:
            sys.stderr = io.StringIO()


STARTUP_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:"Microsoft YaHei",sans-serif;background:#f5f6f7;}
.box{text-align:center;}
.icon{font-size:44px;animation:pulse 1.2s infinite;}
.t1{margin-top:12px;font-size:15px;color:#1f2329;}
.t2{margin-top:6px;font-size:12px;color:#8a9099;}
@keyframes pulse{50%{opacity:.35;}}
</style></head><body>
<div class="box">
  <div class="icon">📖</div>
  <div class="t1">正在启动 embedding…</div>
  <div class="t2">首次加载模型约需 10~60 秒</div>
</div>
</body></html>"""


# --------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="明阴全自动小说")
    parser.add_argument("--web", action="store_true", help="仅启动服务并打开浏览器")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("NOVEL_PORT", "8000"))
    )
    args = parser.parse_args()

    _ensure_console_streams()

    # 1. 启动主服务（内含 /v1/embeddings，模型从程序同路径 models/ 读取）
    from core.embedding_server import preload_embedding
    from server import create_app

    app = create_app(resolve_data_root(), resolve_webui_dir(), resolve_plugins_dir())
    threading.Thread(target=_start_server, args=(app, args.port), daemon=True).start()

    if args.web:
        _wait_ready(args.port)
        webbrowser.open(f"http://127.0.0.1:{args.port}")
        while True:
            time.sleep(3600)

    # 3. 启动窗口：先显示"启动中"，embedding 加载完成后关闭小窗口、打开主窗口
    import webview

    startup = webview.create_window(
        "明阴全自动小说 - 正在启动",
        html=STARTUP_HTML,
        width=360,
        height=200,
        resizable=False,
    )

    def _boot() -> None:
        """后台：加载 embedding 模型 → 等主服务就绪 → 开大窗、关小窗（finally 保证）。"""
        try:
            preload_embedding()
        except Exception as e:
            print(f"embedding 加载失败: {e}")
        finally:
            _wait_ready(args.port)
            try:
                webview.create_window(
                    "明阴全自动小说",
                    f"http://127.0.0.1:{args.port}",
                    width=1100,
                    height=760,
                    min_size=(820, 600),
                )
            except Exception as e:
                print(f"创建主窗口失败: {e}")
            try:
                startup.destroy()
            except Exception as e:
                print(f"关闭启动窗口失败: {e}")

    threading.Thread(target=_boot, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
