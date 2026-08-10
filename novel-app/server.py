"""明阴全自动小说 - 本地服务层（FastAPI）。

对外提供 REST 接口给界面层（Web / pywebview / 手机 WebView），
界面层与功能层（core + plugins）完全分离。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.kernel import NovelKernel
from core.embedding_server import create_embedding_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("novel_app")

# 可配置 LLM API：主对话模型 + 功能 api1/2 + 大纲 api1/2
API_KEYS = ["generator_api", "func_api1", "func_api2", "outline_api1", "outline_api2"]


class ChatIn(BaseModel):
    text: str


class RetryIn(BaseModel):
    pass


class MessageIn(BaseModel):
    content: str


class SettingsIn(BaseModel):
    settings: dict


class NameIn(BaseModel):
    name: str


class TempIn(BaseModel):
    temperature: float


class ModelsIn(BaseModel):
    models: dict


class ServerApp:
    def __init__(
        self,
        data_root: str | Path,
        webui_dir: str | Path | None = None,
        plugins_dir: str | Path | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_root / "config.json"
        self.config = self._load_config()

        self.kernel = NovelKernel(self.config, self.data_root, plugins_dir)
        self._tasks: dict[str, asyncio.Task] = {}  # 会话 -> 进行中的生成任务

        self.app = FastAPI(title="明阴全自动小说", version="1.0.0")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        # 内嵌 embedding（OpenAI 兼容 /v1/embeddings）
        self.app.include_router(create_embedding_router(), prefix="/v1")
        self._register_routes()

        if webui_dir and (Path(webui_dir) / "index.html").exists():
            self.app.mount(
                "/",
                StaticFiles(directory=str(webui_dir), html=True),
                name="webui",
            )

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def _load_config(self) -> dict[str, Any]:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8-sig"))
            except Exception as e:
                logger.error(f"读取 config.json 失败: {e}")
        return {}

    def _save_config(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.kernel.config = self.config

    def _apply_models_to_config(self, models: dict) -> None:
        """把界面模型配置写入内核配置（首个 model 作为默认 model）。"""
        for key in API_KEYS:
            item = models.get(key) or {}
            self.config[key] = {
                "base_url": (item.get("base_url") or "").strip(),
                "api_key": (item.get("api_key") or "").strip(),
                "model": self._first_model(item.get("models", "")),
                "models_list": (item.get("models") or "").strip(),
            }
        self.config["auto_complete_base_url"] = bool(
            models.get("auto_complete_base_url", True)
        )

    @staticmethod
    def _first_model(models: str) -> str:
        for m in str(models or "").split(","):
            m = m.strip()
            if m:
                return m
        return ""

    async def _do_chat(self, sid: str, text: str) -> dict:
        reply = await self.kernel.handle_message(sid, text)
        return {"reply": reply}

    @staticmethod
    def _err_detail(prefix: str, e: Exception) -> str:
        """生成带异常类型与信息的错误详情；str(e) 为空时以类型名兜底。"""
        msg = str(e).strip()
        kind = type(e).__name__
        return f"{prefix}: {kind}: {msg}" if msg else f"{prefix}: {kind}"

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #
    def _register_routes(self) -> None:
        app = self.app

        @app.get("/api/health")
        async def health():
            return {
                "ok": True,
                "plugins": [p.name for p in self.kernel.plugin_manager.plugins],
            }

        # ---- 会话 ----
        @app.get("/api/sessions")
        async def list_sessions():
            sessions = self.kernel.chat_store.list_sessions()
            return [
                {
                    "session_id": s.session_id,
                    "name": s.name,
                    "temperature": s.temperature,
                    "message_count": len(s.messages),
                    "updated_at": s.updated_at,
                }
                for s in sessions
            ]

        @app.post("/api/sessions")
        async def create_session():
            from core.models import ChatSession

            session = ChatSession()
            self.kernel.chat_store.save(session)
            return {"session_id": session.session_id, "name": session.name}

        @app.delete("/api/sessions/{sid}")
        async def delete_session(sid: str):
            self.kernel.chat_store.delete(sid)
            return {"ok": True}

        @app.get("/api/sessions/{sid}")
        async def get_session(sid: str):
            session = self.kernel.chat_store.load(sid)
            if session is None:
                raise HTTPException(404, "会话不存在")
            return {
                "session_id": session.session_id,
                "name": session.name,
                "temperature": session.temperature,
                "settings": session.settings,
                "messages": [
                    {
                        "message_id": m.message_id,
                        "role": m.role,
                        "content": m.content,
                        "created_at": m.created_at,
                    }
                    for m in session.messages
                ],
            }

        @app.post("/api/sessions/{sid}/chat")
        async def chat(sid: str, body: ChatIn):
            old = self._tasks.get(sid)
            if old and not old.done():
                old.cancel()
            task = asyncio.create_task(self._do_chat(sid, body.text))
            self._tasks[sid] = task
            try:
                return await task
            except asyncio.CancelledError:
                raise HTTPException(408, "已停止")
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("chat 处理失败")
                raise HTTPException(500, self._err_detail("处理失败", e))
            finally:
                if self._tasks.get(sid) is task:
                    self._tasks.pop(sid, None)

        @app.post("/api/sessions/{sid}/stop")
        async def stop(sid: str):
            """强行停止当前正在进行的发送/接收。"""
            task = self._tasks.get(sid)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                self._tasks.pop(sid, None)
                return {"ok": True, "stopped": True}
            return {"ok": True, "stopped": False}

        @app.post("/api/sessions/{sid}/retry")
        async def retry(sid: str, body: RetryIn):
            try:
                reply = await self.kernel.retry_last_assistant(sid)
                if not reply:
                    raise HTTPException(400, "没有可重试的消息")
                return {"reply": reply}
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("retry 失败")
                raise HTTPException(500, self._err_detail("重试失败", e))


        # ---- 消息编辑 ----
        @app.patch("/api/messages/{mid}")
        async def edit_message(mid: str, body: MessageIn):
            for s in self.kernel.chat_store.list_sessions():
                if self.kernel.update_message(s.session_id, mid, body.content):
                    return {"ok": True}
            raise HTTPException(404, "消息不存在")

        @app.delete("/api/messages/{mid}")
        async def delete_message(mid: str):
            for s in self.kernel.chat_store.list_sessions():
                if self.kernel.delete_message(s.session_id, mid):
                    return {"ok": True}
            raise HTTPException(404, "消息不存在")

        # ---- 会话设置 / 命令表 ----
        @app.get("/api/settings/commands")
        async def setting_commands():
            return {"commands": self.kernel.get_setting_commands()}

        @app.get("/api/sessions/{sid}/settings")
        async def get_settings(sid: str):
            session = self.kernel.chat_store.load(sid)
            if session is None:
                raise HTTPException(404, "会话不存在")
            return {
                "settings": session.settings,
                "temperature": session.temperature,
                "name": session.name,
            }

        @app.patch("/api/sessions/{sid}/settings")
        async def patch_settings(sid: str, body: SettingsIn):
            session = self.kernel.chat_store.load(sid)
            if session is None:
                raise HTTPException(404, "会话不存在")
            session.settings.update(body.settings)
            self.kernel.chat_store.save(session)
            return {"ok": True}

        @app.patch("/api/sessions/{sid}/name")
        async def patch_name(sid: str, body: NameIn):
            session = self.kernel.chat_store.load(sid)
            if session is None:
                raise HTTPException(404, "会话不存在")
            session.name = body.name
            self.kernel.chat_store.save(session)
            return {"ok": True}

        @app.patch("/api/sessions/{sid}/temperature")
        async def patch_temp(sid: str, body: TempIn):
            session = self.kernel.chat_store.load(sid)
            if session is None:
                raise HTTPException(404, "会话不存在")
            session.temperature = max(0.0, min(2.0, body.temperature))
            self.kernel.chat_store.save(session)
            return {"ok": True}

        # ---- 模型配置 ----
        @app.get("/api/models")
        async def get_models():
            models = {}
            for key in API_KEYS:
                item = self.config.get(key) or {}
                models[key] = {
                    "base_url": item.get("base_url", ""),
                    "api_key": item.get("api_key", ""),
                    "models": item.get("models_list") or item.get("model", ""),
                }
            return {
                "models": models,
                "auto_complete_base_url": self.config.get(
                    "auto_complete_base_url", True
                ),
            }

        @app.put("/api/models")
        async def put_models(body: ModelsIn):
            self._apply_models_to_config(body.models)
            self._save_config()
            from core.generators import Generator

            self.kernel.generator = Generator(self.config)
            return {"ok": True}

        # ---- 插件 ----
        @app.get("/api/plugins")
        async def get_plugins():
            return {
                "table": self.kernel.plugin_manager.get_table(),
                "loaded": [
                    {
                        "name": p.name,
                        "priority": p.priority,
                        "description": p.description,
                    }
                    for p in self.kernel.plugin_manager.plugins
                ],
            }

        @app.post("/api/plugins/reload")
        async def reload_plugins():
            result = self.kernel.reload_plugins()
            return {
                "result": result,
                "settings_commands": self.kernel.get_setting_commands(),
            }

        # ---- 知识库套 ----
        @app.get("/api/kbsets")
        async def list_kbsets():
            for p in self.kernel.plugin_manager.plugins:
                if p.name == "novel":
                    return {"kbsets": p.kb_manager.list_sets()}
            return {"kbsets": []}


def create_app(
    data_root: str | Path,
    webui_dir: str | Path | None = None,
    plugins_dir: str | Path | None = None,
) -> FastAPI:
    return ServerApp(data_root, webui_dir, plugins_dir).app
