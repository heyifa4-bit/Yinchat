"""对话存储能力：以读取方便的 JSON 格式持久化对话。

存储目录：<data_root>/chats/<session_id>.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import ChatMessage, ChatSession

logger = logging.getLogger("novel_app")


class ChatStore:
    def __init__(self, data_root: str | Path) -> None:
        self.chats_dir = Path(data_root) / "chats"
        self.chats_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        # 会话 id 仅允许安全字符，防止路径穿越
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return self.chats_dir / f"{safe}.json"

    def save(self, session: ChatSession) -> None:
        path = self._path(session.session_id)
        path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
            errors="replace",
        )

    def load(self, session_id: str) -> ChatSession | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            logger.error(f"读取对话失败 {session_id}: {e}")
            return None
        session = ChatSession(
            session_id=data.get("session_id", session_id),
            name=data.get("name", "新对话"),
            temperature=float(data.get("temperature", 0.8)),
            settings=data.get("settings", {}) or {},
            kbset=data.get("kbset", "default"),
        )
        for m in data.get("messages", []) or []:
            session.messages.append(
                ChatMessage(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    message_id=m.get("message_id", ""),
                    meta=m.get("meta", {}) or {},
                )
            )
        session.created_at = data.get("created_at", session.created_at)
        session.updated_at = data.get("updated_at", session.updated_at)
        return session

    def get_or_create(self, session_id: str) -> ChatSession:
        session = self.load(session_id)
        if session is None:
            session = ChatSession(session_id=session_id)
        return session

    def list_sessions(self) -> list[ChatSession]:
        sessions = []
        for f in self.chats_dir.glob("*.json"):
            try:
                s = self.load(f.stem)
                if s:
                    sessions.append(s)
            except Exception:
                continue
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            path.unlink()
