"""数据模型：对话会话与消息。"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ChatMessage:
    """单条消息。"""

    role: str  # "user" / "assistant"
    content: str
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)  # 额外信息（如冲突标签等）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChatSession:
    """一个对话会话。"""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "新对话"
    temperature: float = 0.8
    settings: dict = field(default_factory=dict)  # 单对话设置（由设置命令表驱动）
    kbset: str = "default"  # 当前使用的知识库套
    messages: list = field(default_factory=list)  # list[ChatMessage]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_message(self, role: str, content: str, meta: dict | None = None) -> ChatMessage:
        msg = ChatMessage(role=role, content=content, meta=meta or {})
        self.messages.append(msg)
        self.updated_at = time.time()
        return msg

    def to_dict(self) -> dict:
        return asdict(self)
