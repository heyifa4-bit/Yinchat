"""通用拦截器（唯一）。

只负责三件事：
1. 拦截：接住消息，构造上下文；
2. 分发：按插件表顺序依次把上下文发送给各插件（before_generate / after_generate）；
3. 接收返回并进入下一步工作流：插件可改写 prompt/history/reply、置 consume/stop。
"""

from __future__ import annotations

from .plugin_base import PluginContext


class Interceptor:
    """通用拦截器：拦截 → 依插件表依次发送给插件 → 接受返回 → 下一步。"""

    def __init__(self, kernel) -> None:
        self.kernel = kernel

    async def run_before(self, ctx: PluginContext) -> None:
        """生成前：按插件表顺序调用各插件 before_generate。"""
        for plugin in self.kernel.plugin_manager.plugins:
            if ctx.consume or ctx.stop:
                break
            await plugin.before_generate(ctx)

    async def run_after(self, ctx: PluginContext) -> None:
        """生成后：按插件表顺序调用各插件 after_generate。"""
        for plugin in self.kernel.plugin_manager.plugins:
            if ctx.stop:
                break
            await plugin.after_generate(ctx)
