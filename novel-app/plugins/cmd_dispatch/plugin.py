"""命令分发插件：消息以 // 开头时置空（不进 LLM），并路由到各插件处理命令。

本插件自身提供：//help、//status、//temp、//name。
其他插件（如 novel）通过 handle_command 提供自己的命令。
"""

from __future__ import annotations

from core.plugin_base import Plugin, PluginContext

_HELP = """📖 明阴全自动小说 命令（消息以 // 开头）：
  //help                      显示帮助
  //status                    查看当前状态与知识库
  //temp <数值>               设置本对话温度（0~2）
  //name <对话名>             设置本对话显示名
  //kb list|create|add|set|remove
  //sqlite create|table|insert|update|delete|show
  //fixed <writing|outline|modify> <内容>
  //outline update            更新分卷（生成卷纲）
  //update                    命令更新知识库
  //export                    导出对话到 txt
  //export-kb                 导出全部知识库到 txt
  //kbset <套名>              切换/创建知识库套
  //kbset-list                列出所有知识库套
（更详细用法输入对应命令即可查看）"""


class CmdDispatchPlugin(Plugin):
    name = "cmd_dispatch"
    priority = 5
    description = "// 命令分发（置空不进 LLM）"

    async def before_generate(self, ctx: PluginContext) -> None:
        text = ctx.user_text.strip()
        if not text.startswith("//"):
            return
        body = text[2:].strip()
        cmd, _, arg = body.partition(" ")
        # 依次让各插件处理命令（含本插件自身）
        result = await ctx.kernel.dispatch_command(ctx.session, cmd, arg.strip())
        if result is None:
            result = f"未知命令: //{cmd}"
        ctx.reply = result
        ctx.consume = True  # 置空，不进入 LLM

    async def handle_command(self, session, cmd: str, arg: str) -> str | None:
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler is None:
            return None
        try:
            return await handler(session, arg)
        except Exception as e:
            return f"❌ 命令执行失败: {e}"

    # ------------------------------------------------------------------ #
    async def _cmd_help(self, session, arg: str) -> str:
        return _HELP

    async def _cmd_temp(self, session, arg: str) -> str:
        try:
            val = float(arg.strip())
        except (TypeError, ValueError):
            return "用法: //temp <数值>（0~2）"
        val = max(0.0, min(2.0, val))
        session.temperature = val
        self.kernel.chat_store.save(session)
        return f"✅ 本对话温度已设为 {val}"

    async def _cmd_name(self, session, arg: str) -> str:
        if not arg.strip():
            return "用法: //name <对话名>"
        session.name = arg.strip()
        self.kernel.chat_store.save(session)
        return f"✅ 对话名已设为: {session.name}"

    async def _cmd_status(self, session, arg: str) -> str:
        # 状态信息由各插件聚合：基础信息 + 插件列表 + 设置
        lines = ["📖 明阴全自动小说 状态"]
        lines.append(f"- 对话: {session.name}")
        lines.append(f"- 温度: {session.temperature}")
        for plugin in self.kernel.plugin_manager.plugins:
            lines.append(
                f"- 插件[{plugin.priority}]: {plugin.name} - {plugin.description}"
            )
        lines.append("")
        lines.append("⚙️ 当前会话设置:")
        for item in self.kernel.get_setting_commands():
            key = item.get("key")
            lines.append(f"- {item.get('label', key)}: {session.settings.get(key)}")
        lines.append("")
        lines.append("使用 //help 查看全部命令。")
        return "\n".join(lines)
