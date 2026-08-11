"""明阴全自动小说 - API 客户端模块。

提供 OpenAI 兼容的异步 LLM Chat 调用与 Embedding 调用，
插件内部使用的 api1/api2/大纲api1/大纲api2/embedding 均由此模块发出。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

import logging

logger = logging.getLogger("novel_app")

_DEFAULT_TIMEOUT = httpx.Timeout(180.0, connect=20.0)


class ApiClient:
    """OpenAI 兼容 API 客户端（用于 Chat Completions 与 Embeddings）。"""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: httpx.Timeout | None = None,
        name: str = "api",
    ) -> None:
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.name = name
        self.timeout = timeout or _DEFAULT_TIMEOUT

    # ------------------------------------------------------------------ #
    # 工具方法
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def is_configured(self) -> bool:
        return bool(self.api_key or self.base_url)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self._url(path),
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"[{self.name}] API 请求失败 (HTTP {resp.status_code}): "
                    f"{resp.text[:500]}"
                )
            try:
                data = resp.json()
            except Exception:
                raise RuntimeError(
                    f"[{self.name}] API 返回非 JSON 内容: {resp.text[:300]}"
                )
            return data

    # ------------------------------------------------------------------ #
    # Chat Completions
    # ------------------------------------------------------------------ #
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        response_format: dict | None = None,
    ) -> str:
        """调用 Chat Completions 并返回 assistant 的文本内容。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        data = await self._post("chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"[{self.name}] 无法解析 Chat 响应: {data}")

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """调用 Chat Completions，并尽力将输出解析为 JSON 字典。

        先尝试 response_format=json_object；若解析失败则从文本中
        提取首段 JSON 代码块 / 大括号块。
        """
        raw = await self.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return parse_json_object(raw)

    # ------------------------------------------------------------------ #
    # Embeddings
    # ------------------------------------------------------------------ #
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """调用 Embeddings 接口，返回文本向量列表。"""
        if not texts:
            return []
        payload = {"model": self.model, "input": texts}
        data = await self._post("embeddings", payload)
        try:
            items = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in items]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"[{self.name}] 无法解析 Embedding 响应: {data}")

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


def parse_json_object(raw: str) -> dict[str, Any]:
    """尽力从 LLM 输出文本中解析出 JSON 对象。"""
    if not raw:
        return {}
    raw = raw.strip()
    # 去掉 ```json ... ``` 包裹
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # 尝试截取第一个 { 到最后一个 }
    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


async def call_with_retry(
    func,
    *args,
    retries: int = 3,
    delay: float = 1.5,
    **kwargs,
):
    """带简单指数退避重试的异步调用包装。"""
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < retries - 1:
                logger.warning(f"API 调用失败，重试 {attempt + 1}/{retries}: {e}")
                await asyncio.sleep(delay * (2**attempt))
    raise last_exc  # type: ignore[misc]
