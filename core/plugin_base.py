"""插件基类与插件上下文。

主体（内核）只做收发改拦；所有能力由插件实现。
插件通过继承 Plugin 并实现相应钩子接入工作流。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .kernel import NovelKernel
    from .models import ChatSession

logger = logging.getLogger("novel_app")


class PluginContext:
    """一次消息处理流程的上下文，贯穿 插件(拦截)→生成端→插件(后处理)。"""

    def __init__(
        self,
        kernel: "NovelKernel",
        session: "ChatSession",
        user_text: str,
    ) -> None:
        self.kernel = kernel
        self.session = session
        self.user_text = user_text          # 原始用户输入
        self.prompt = user_text             # 当前要发给生成端的提示词（插件可改写）
        self.history: str = ""              # 格式化后的上文（会话历史插件填充）
        self.background: str = ""           # 注入背景（小说插件填充）
        self.reply: str = ""                # 生成端回复
        self.consume: bool = False          # 消费：置空，不进入 LLM（如 // 命令）
        self.stop: bool = False             # 终止整条流程
        self.extras: dict[str, Any] = {}    # 插件间共享数据


class Plugin:
    """插件基类。priority 数字越小，处理顺序越靠前。"""

    name: str = "plugin"
    priority: int = 100
    description: str = ""
    version: str = "1.0.0"
    # 插件的简单"开-关"设置命令表：
    # [{"key": "xxx", "label": "显示名", "type": "bool|int|float|string|select",
    #   "options": [...], "default": ..., "hint": "..."}]
    command_table: list[dict[str, Any]] = []

    def __init__(self, kernel: "NovelKernel") -> None:
        self.kernel = kernel

    def on_load(self) -> None:
        """插件加载时调用（可选）。"""

    def on_unload(self) -> None:
        """插件卸载时调用（可选）。"""

    async def before_generate(self, ctx: PluginContext) -> None:
        """生成端调用前执行（按 priority 升序）。"""

    async def after_generate(self, ctx: PluginContext) -> None:
        """生成端返回后执行（按 priority 升序）。"""

    async def handle_command(
        self, session: "ChatSession", cmd: str, arg: str
    ) -> str | None:
        """复杂命令（//xxx）处理。返回 None = 不处理此命令，交给下一个插件。"""
        return None
