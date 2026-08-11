"""最终内容生成端能力：调用上游 LLM 生成章节内容。

独立化后由软件自己调用写小说的 AI（此前由 AstrBot 主 provider 承担）。
默认使用功能 api1 的配置，也可单独配置 generator_api 覆盖。
"""

from __future__ import annotations

import logging
from typing import Any

from .api_client import ApiClient, call_with_retry
from .plugin_base import PluginContext

logger = logging.getLogger("novel_app")


class Generator:
    """最终内容生成端。"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        gen = config.get("generator_api") or config.get("func_api1") or {}
        self.client = ApiClient(
            gen.get("base_url", ""),
            gen.get("api_key", ""),
            gen.get("model", ""),
            name="生成端",
        )

    def is_configured(self) -> bool:
        return bool(self.client.is_configured())

    async def generate(self, ctx: PluginContext) -> str:
        """根据拦截后的上下文调用生成端，返回文本回复。"""
        if not self.is_configured():
            raise RuntimeError("最终生成端未配置（请配置功能 api1 或 generator_api）")
        temperature = float(getattr(ctx.session, "temperature", 0.8) or 0.8)
        messages: list[dict[str, Any]] = []
        # 系统提示由插件注入（如小说插件可在背景中包含写作要求）
        system_prompt = ctx.extras.get("system_prompt", "")
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        # 历史上下文
        if ctx.history:
            messages.append({"role": "user", "content": ctx.history})
        # 当前注入后的提示词
        messages.append({"role": "user", "content": ctx.prompt})
        try:
            return await call_with_retry(
                self.client.chat, messages, temperature=temperature
            )
        except Exception as e:
            logger.error(f"最终生成端调用失败: {e}")
            raise
