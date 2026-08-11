"""内嵌 Embedding 服务（OpenAI 兼容 /v1/embeddings）。

单 exe 内嵌方案：模型在主进程内全局加载（preload_embedding），
通过 create_embedding_router() 挂载到主服务 /v1 路径下。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("novel_app")


def resolve_model_dir() -> str:
    """解析模型目录：一律从程序同路径 models/bge-m3-local 读取。

    - exe 模式：exe 所在目录的 models/bge-m3-local；
    - 源码模式：项目根目录的 models/bge-m3-local。
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    p = base / "models" / "bge-m3-local"
    if p.exists() and (p / "config.json").exists():
        return str(p)
    return ""


# --------------------------------------------------------------------- #
# 全局模型状态（单例）
# --------------------------------------------------------------------- #
_STATE: dict = {
    "model": None,
    "dir": "",
    "device": "cpu",
    "ready": False,
    "error": "",
}


def is_ready() -> bool:
    return _STATE["ready"]


def _load() -> None:
    """同步加载模型到全局状态。"""
    if _STATE["ready"]:
        return
    d = resolve_model_dir()
    if not d:
        raise RuntimeError("未找到 embedding 模型目录（bge-m3-local）")
    from sentence_transformers import SentenceTransformer

    dev = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            dev = "cuda"
    except Exception:
        dev = "cpu"
    _STATE["model"] = SentenceTransformer(d, device=dev)
    _STATE["dir"] = d
    _STATE["device"] = dev
    _STATE["ready"] = True
    _STATE["error"] = ""
    logger.info(f"embedding 模型加载完成: {d} ({dev})")


def preload_embedding() -> None:
    """主动加载模型（启动窗口期间调用，阻塞直到就绪或抛错）。"""
    try:
        _load()
    except Exception as e:
        _STATE["error"] = str(e)
        logger.error(f"embedding 模型加载失败: {e}")
        raise


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """供主服务内部/插件调用：把文本列表转为向量。"""
    if _STATE["model"] is None:
        await asyncio.to_thread(_load)
    vecs = await asyncio.to_thread(
        _STATE["model"].encode, texts, normalize_embeddings=True
    )
    return vecs.tolist()


# --------------------------------------------------------------------- #
# OpenAI 兼容路由（挂载到主服务 /v1）
# --------------------------------------------------------------------- #
def create_embedding_router() -> APIRouter:
    router = APIRouter()

    @router.post("/embeddings")
    async def embeddings(req: Request) -> JSONResponse:
        try:
            data = await req.json()
        except Exception:
            return JSONResponse({"error": {"message": "invalid JSON"}}, 400)
        texts = data.get("input")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            return JSONResponse(
                {"error": {"message": "input must be string or list"}}, 400
            )
        if _STATE["model"] is None:
            try:
                await asyncio.to_thread(_load)
            except Exception as e:
                return JSONResponse({"error": {"message": str(e)}}, 500)
        vecs = await asyncio.to_thread(
            _STATE["model"].encode, texts, normalize_embeddings=True
        )
        vectors = vecs.tolist()
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": v}
                    for i, v in enumerate(vectors)
                ],
                "model": "bge-m3-local",
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            }
        )

    @router.get("/models")
    async def list_models():
        return {
            "object": "list",
            "data": [
                {
                    "id": "bge-m3-local",
                    "object": "model",
                    "owned_by": "local",
                    "dir": _STATE["dir"],
                    "ready": _STATE["ready"],
                    "error": _STATE["error"],
                }
            ],
        }

    return router
