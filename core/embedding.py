"""Embedding 调用封装：暴露给所有插件使用。

- 默认端点：http://127.0.0.1:8000/v1/embeddings（exe 启动时自动拉起的内嵌服务）
- 后来的插件可 `from core.embedding import embed_texts` 直接调用；
- 原小说插件（plugins/novel）保持原有调用方式，不受影响。
"""

from __future__ import annotations

import httpx

from .api_client import call_with_retry

DEFAULT_ENDPOINT = "http://127.0.0.1:8000/v1/embeddings"
MODELS_ENDPOINT = "http://127.0.0.1:8000/v1/models"


async def embed_texts(
    texts: list[str], endpoint: str | None = None
) -> list[list[float]]:
    """把文本列表转为向量（默认内嵌 embedding 服务）。"""
    if not texts:
        return []
    url = endpoint or DEFAULT_ENDPOINT

    async def _post():
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            resp = await client.post(url, json={"input": texts})
            resp.raise_for_status()
            data = resp.json()
            items = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in items]

    return await call_with_retry(_post)


async def embed_text(text: str, endpoint: str | None = None) -> list[float]:
    """把单条文本转为向量。"""
    return (await embed_texts([text], endpoint))[0]


async def ping(timeout: float = 2.0) -> bool:
    """探测内嵌 embedding 服务是否就绪。"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(MODELS_ENDPOINT)
            return resp.status_code == 200
    except Exception:
        return False


async def wait_ready(timeout: float = 120.0, interval: float = 0.5) -> bool:
    """等待内嵌 embedding 服务就绪。"""
    import asyncio

    waited = 0.0
    while waited < timeout:
        if await ping(1.0):
            return True
        await asyncio.sleep(interval)
        waited += interval
    return False
