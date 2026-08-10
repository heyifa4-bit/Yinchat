"""主体内核：只做最基础的收发改拦，所有能力由插件实现。

- 收：接收用户消息；
- 拦：通用拦截器按插件表顺序发送给各插件；
- 发：调最终生成端（上游 LLM）；
- 改：插件可改写上下文；
- 插件管理：reload_plugins() 重载，聚合设置命令表。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .chat_store import ChatStore
from .generators import Generator
from .interceptors import Interceptor
from .models import ChatSession
from .plugin_base import PluginContext
from .plugin_manager import PluginManager

logger = logging.getLogger("novel_app")


class NovelKernel:
    """明阴全自动小说 - 独立软件主体内核。"""

    def __init__(
        self,
        config: dict[str, Any],
        data_root: str | Path,
        plugins_dir: str | Path | None = None,
    ) -> None:
        self.config = config
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)

        if plugins_dir is None:
            plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
        self.plugins_dir = Path(plugins_dir)

        self.chat_store = ChatStore(self.data_root)
        self.generator = Generator(config)
        self.plugin_manager = PluginManager(self, self.plugins_dir)
        self.interceptor = Interceptor(self)

        self.settings_commands: list[dict[str, Any]] = []
        self.reload_plugins()

    # ------------------------------------------------------------------ #
    # 插件管理 / 重载
    # ------------------------------------------------------------------ #
    def reload_plugins(self) -> dict:
        """重载插件：扫描插件文件夹，更新插件表与设置命令表。"""
        result = self.plugin_manager.reload()
        self.settings_commands = self.plugin_manager.get_setting_commands()
        logger.info(
            f"插件重载完成: 加载={result['loaded']} 移除={result['removed']} "
            f"失败={result['failed']}；设置命令表共 {len(self.settings_commands)} 项"
        )
        return result

    def get_setting_commands(self) -> list[dict[str, Any]]:
        """聚合后的简单开关设置命令表（供单对话设置界面渲染）。"""
        return list(self.settings_commands)

    def merge_session_settings(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        """把会话设置与命令表默认值合并，保证键完整。"""
        merged: dict[str, Any] = {}
        for item in self.settings_commands:
            key = item.get("key")
            if key:
                merged[key] = (settings or {}).get(key, item.get("default"))
        if settings:
            for k, v in settings.items():
                if k not in merged:
                    merged[k] = v
        return merged

    # ------------------------------------------------------------------ #
    # 消息处理
    # ------------------------------------------------------------------ #
    async def handle_message(self, session_id: str, user_text: str) -> str:
        """处理一条用户消息，返回回复文本（会话已持久化）。"""
        session = self.chat_store.get_or_create(session_id)
        reply = await self._generate_reply(session, user_text)
        session.add_message("user", user_text)
        session.add_message("assistant", reply)
        self.chat_store.save(session)
        return reply

    async def _generate_reply(self, session: ChatSession, user_text: str) -> str:
        """执行拦截管道 + 生成端，返回回复文本（不修改会话存储）。"""
        session.settings = self.merge_session_settings(session.settings)

        ctx = PluginContext(self, session, user_text)
        await self.interceptor.run_before(ctx)

        if not ctx.consume and not ctx.stop:
            ctx.reply = await self.generator.generate(ctx)

        await self.interceptor.run_after(ctx)
        return ctx.reply

    # ------------------------------------------------------------------ #
    # 命令分发
    # ------------------------------------------------------------------ #
    async def dispatch_command(
        self, session: ChatSession, cmd: str, arg: str
    ) -> str:
        """把 // 复杂命令依次交给各插件处理。"""
        result = await self.plugin_manager.dispatch_command(session, cmd, arg)
        if result is not None:
            return result
        return f"未知命令: //{cmd}"

    # ------------------------------------------------------------------ #
    # 会话编辑
    # ------------------------------------------------------------------ #
    async def retry_last_assistant(self, session_id: str) -> str:
        """AI 回复重试：删除最后一条 assistant，基于原用户消息重新生成。"""
        session = self.chat_store.get_or_create(session_id)
        if session.messages and session.messages[-1].role == "assistant":
            session.messages.pop()
        if session.messages and session.messages[-1].role == "user":
            last_user = session.messages[-1].content
            reply = await self._generate_reply(session, last_user)
            session.add_message("assistant", reply)
            self.chat_store.save(session)
            return reply
        return ""

    def delete_message(self, session_id: str, message_id: str) -> bool:
        session = self.chat_store.load(session_id)
        if not session:
            return False
        before = len(session.messages)
        session.messages = [m for m in session.messages if m.message_id != message_id]
        if len(session.messages) != before:
            self.chat_store.save(session)
            return True
        return False

    def update_message(self, session_id: str, message_id: str, content: str) -> bool:
        session = self.chat_store.load(session_id)
        if not session:
            return False
        for m in session.messages:
            if m.message_id == message_id:
                m.content = content
                self.chat_store.save(session)
                return True
        return False
