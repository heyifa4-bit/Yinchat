"""会话历史插件：读取当前对话，把指定数量的上下文按固定格式置入对话首段。

格式：
#####
#USER:******
#AI:******
#####
"""

from core.plugin_base import Plugin, PluginContext


class HistoryPlugin(Plugin):
    name = "history"
    priority = 10
    description = "会话历史注入（固定格式置入对话首段）"
    command_table = [
        {
            "key": "history_count",
            "label": "注入上文条数",
            "type": "int",
            "default": 20,
            "hint": "置入对话首段的消息条数",
        }
    ]

    async def before_generate(self, ctx: PluginContext) -> None:
        try:
            count = int(ctx.session.settings.get("history_count", 20) or 20)
        except (TypeError, ValueError):
            count = 20
        msgs = ctx.session.messages
        if count <= 0 or not msgs:
            return
        tail = msgs[-count:]
        parts = ["#####"]
        for m in tail:
            label = "USER" if m.role == "user" else "AI"
            parts.append(f"#{label}:{m.content}")
        parts.append("#####")
        ctx.history = "\n".join(parts)
