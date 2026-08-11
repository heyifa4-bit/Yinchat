"""明阴全自动小说 - 核心业务逻辑模块。

负责：主知识库判别、嵌套知识库检索与背景注入、章纲/卷纲生成、
内容审核、知识库自动更新（命令 / 逐章）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("novel_app")

from core.api_client import ApiClient, call_with_retry
from plugins.novel.kb_store import KBStore, strip_once_marks

# --------------------------------------------------------------------- #
# 系统提示词
# --------------------------------------------------------------------- #
MASTER_LLM_SYSTEM = """你是小说创作辅助系统的主知识库判别器。
我会给你：1. 用户本轮的对话内容；2. 所有知识库的识别名与描述。
请判断本轮创作需要用到的知识库，只输出 JSON：{"kbs": ["名称1", "名称2"]}。
只选择确实相关的知识库，不要全部输出。"""

MASTER_LLM_USER = """所有知识库列表：
{master_txt}

本对话：
{conversation}
"""

AUDIT_SYSTEM = """你是小说创作辅助系统的内容审核器。
我会给你：1. 用户输入（含注入的写作背景）；2. AI 的回复。
请判断 AI 回复与用户输入是否存在明显冲突（如与注入背景设定矛盾、与当前卷纲矛盾、
与前文历史线矛盾、人设崩塌、剧情前后不一致等）。
只输出 JSON：{"conflict": true/false, "detail": "冲突说明（无冲突则为空字符串）"}"""

AUDIT_USER = """用户输入：
{input_text}

AI 回复：
{output_text}
"""

OUTLINE_SYSTEM = """你是小说创作辅助系统的章纲编写器。
请基于用户给出的上下文（含注入的写作背景）编写本章章纲。
章纲要求：包含本章目标、关键情节点（分步骤）、登场角色、需要强调的伏笔/细节。
只输出 JSON：{"outline": "章纲内容"}"""

VOLUME_SYSTEM = """你是小说创作辅助系统的卷纲编写器。
请基于已总结的剧情，为下一卷编写卷纲。
卷纲要求：包含卷的主题、主要剧情走向、主要登场角色、关键事件、卷末高潮。
如果用户提供了补充要求（如卷标题、章节数、风格等），必须严格遵循。
只输出 JSON：{"volume_outline": "卷纲内容"}"""

SUMMARY_SYSTEM = """你是小说创作辅助系统的剧情总结器。
请将对话历史中的剧情进行精炼总结，保留：主要事件、人物关系变化、当前状态、未解决伏笔。
只输出 JSON：{"summary": "总结内容"}"""

UPDATE_COMMAND_SYSTEM = """你是小说创作辅助系统的知识库维护器。
我会给你：1. 当前所有知识库的识别名；2. 全部知识库内容；3. 预设工具命令表；
4. 预设修改要求固定文本；5. 剧情总结。
请根据剧情总结，判断需要更新的知识库元素，并输出 JSON：
{"updates": [{"command": "sqlite_insert", "args": {...}}, ...]}
必须严格遵守预设修改要求与工具命令表。"""

UPDATE_PER_CHAPTER_SYSTEM = """你是小说创作辅助系统的知识库更新判断器。
我会给你：1. 发送给 AI 的本对话（含注入背景）；2. AI 的回复。
请判断是否需要更新知识库（出现新角色、角色状态变化、重大剧情推进、新设定出现等）。
需要更新时输出 JSON：{"need_update": true, "updates": [{"command": "...", "args": {...}}, ...]}
不需要更新时输出 JSON：{"need_update": false}"""

class NovelService:
    """小说写作辅助的核心服务。"""

    def __init__(self, config: Any, kb_store: KBStore) -> None:
        self.config = config
        self.kb = kb_store

        # 四个 LLM API + 一个 embedding API
        c = config.get
        f1 = c("func_api1", {})
        f2 = c("func_api2", {})
        o1 = c("outline_api1", {})
        o2 = c("outline_api2", {})
        e = c("embedding_api", {})
        self.func_client1 = ApiClient(
            f1.get("base_url", ""),
            f1.get("api_key", ""),
            f1.get("model", ""),
            name="功能api1",
        )
        self.func_client2 = ApiClient(
            f2.get("base_url", ""),
            f2.get("api_key", ""),
            f2.get("model", ""),
            name="功能api2",
        )
        self.outline_client1 = ApiClient(
            o1.get("base_url", ""),
            o1.get("api_key", ""),
            o1.get("model", ""),
            name="大纲api1",
        )
        self.outline_client2 = ApiClient(
            o2.get("base_url", ""),
            o2.get("api_key", ""),
            o2.get("model", ""),
            name="大纲api2",
        )
        emb_url = e.get("base_url") or "http://127.0.0.1:8000/v1"
        self.embedding_client = ApiClient(
            emb_url,
            e.get("api_key", ""),
            e.get("model", ""),
            name="embedding",
        )

    # ------------------------------------------------------------------ #
    # 客户端选择
    # ------------------------------------------------------------------ #
    def get_func_client(self) -> ApiClient:
        """多功能 API 模式开启时使用功能 api2，否则功能 api1。"""
        if self.config.get("multi_func_api_mode", False):
            return self.func_client2
        return self.func_client1

    def get_outline_client(self) -> ApiClient:
        """多大纲 API 模式开启时使用大纲 api2，否则大纲 api1。"""
        if self.config.get("multi_outline_api_mode", False):
            return self.outline_client2
        return self.outline_client1

    # ------------------------------------------------------------------ #
    # Embedding
    # ------------------------------------------------------------------ #
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量计算文本向量。默认使用内嵌本地 embedding 服务。"""
        return await call_with_retry(self.embedding_client.embed, texts)

    async def embed_text(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]


    # ------------------------------------------------------------------ #
    # 主知识库判别
    # ------------------------------------------------------------------ #
    async def select_knowledge_by_master(
        self, conversation_text: str
    ) -> list[str]:
        """根据主知识库判别需要检索的知识库识别名。"""
        mode = self.config.get("retrieve_mode", "embedding")
        top_k = int(self.config.get("master_top_k", 3))
        threshold = float(self.config.get("master_threshold", 0.35))

        if mode == "llm":
            client = self.get_func_client()
            if not client.is_configured():
                raise RuntimeError("llm 判别模式需要配置功能 api1/api2")
            raw = await call_with_retry(
                client.chat_json,
                [
                    {"role": "system", "content": MASTER_LLM_SYSTEM},
                    {
                        "role": "user",
                        "content": MASTER_LLM_USER.format(
                            master_txt=self.kb.get_master_txt(),
                            conversation=conversation_text[:6000],
                        ),
                    },
                ],
                max_tokens=2048,
            )
            names = raw.get("kbs", []) if isinstance(raw, dict) else []
            return [str(n).strip() for n in names if str(n).strip()]
        else:
            # embedding 模式
            query_vec = await self.embed_text(conversation_text[:4000])
            entries = self.kb._master_entries()
            # 实时补齐缺失的主知识库向量
            for e in entries:
                if e.get("vector"):
                    continue
                try:
                    vec = await self.embed_text(f"{e['name']} {e['desc']}")
                    self.kb.update_master_vector(e["name"], vec)
                    e["vector"] = vec
                except Exception:
                    continue
            scored: list[tuple[float, str]] = []
            for e in entries:
                vec = e.get("vector") or []
                if not vec:
                    continue
                sim = _cosine_score(query_vec, vec)
                scored.append((sim, e["name"]))
            scored.sort(key=lambda x: x[0], reverse=True)
            selected = [
                name for sim, name in scored[:top_k] if sim >= threshold
            ]
            if not selected and scored:
                logger.debug("主知识库 embedding 判别未选中任何知识库")
            return selected

    # ------------------------------------------------------------------ #
    # 背景注入
    # ------------------------------------------------------------------ #
    async def build_background(self, conversation_text: str) -> str:
        """构建注入的写作背景文本。

        返回形如：
        写作背景
        # 写作要求
        ...
        # 当前卷纲
        ...
        # 角色与历史
        ...
        # 背景·xxx
        ...
        """
        parts: list[str] = ["写作背景"]

        # 固定文本始终加入
        writing = self.kb.get_fixed("writing").strip()
        if writing:
            parts.append("# 写作要求")
            parts.append(writing)
        outline = self.kb.get_fixed("outline").strip()
        if outline:
            parts.append("# 当前卷纲")
            parts.append(outline)

        # 主知识库判别（可关闭 -> 全量检索）
        full_scan = self.config.get("full_scan", False)
        try:
            if full_scan:
                selected = [e["name"] for e in self.kb._master_entries()]
            else:
                selected = await self.select_knowledge_by_master(conversation_text)
        except Exception as e:
            logger.error(f"[明阴全自动小说] 主知识库判别失败: {e}")
            selected = []

        # sqlite 匹配：拿主键去文本中检索
        sqlite_names = [
            s for s in selected if self.kb._entry_kind(s) == "sqlite"
        ]
        try:
            sqlite_hits = self.kb.match_sqlite_by_keys(conversation_text, sqlite_names)
        except Exception as e:
            logger.error(f"[明阴全自动小说] sqlite 匹配失败: {e}")
            sqlite_hits = []
        if sqlite_hits:
            parts.append("# 角色与历史")
            parts.extend(sqlite_hits)

        # embedding 知识库检索
        emb_names = [
            s for s in selected if self.kb._entry_kind(s) == "embedding"
        ]
        if emb_names:
            try:
                query_vec = await self.embed_text(conversation_text[:4000])
            except Exception as e:
                logger.error(f"[明阴全自动小说] 检索向量计算失败: {e}")
                query_vec = None
            if query_vec:
                top_k = int(self.config.get("embedding_top_k", 3))
                for kb_name in emb_names:
                    try:
                        results = self.kb.search_embedding_kb(
                            kb_name, query_vec, top_k
                        )
                    except Exception as e:
                        logger.error(
                            f"[明阴全自动小说] 知识库 {kb_name} 检索失败: {e}"
                        )
                        continue
                    if results:
                        parts.append(f"# 背景·{kb_name}")
                        parts.extend(r["text"] for r in results)

        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    # 章纲 / 卷纲
    # ------------------------------------------------------------------ #
    async def build_chapter_outline(self, injected_prompt: str) -> str:
        """基于注入背景后的对话生成章纲。"""
        client = self.get_outline_client()
        if not client.is_configured():
            raise RuntimeError("章纲生成需要配置大纲 api1/api2")
        raw = await call_with_retry(
            client.chat_json,
            [
                {"role": "system", "content": OUTLINE_SYSTEM},
                {"role": "user", "content": injected_prompt[:12000]},
            ],
            max_tokens=2048,
        )
        outline = (
            str(raw.get("outline", "")).strip() if isinstance(raw, dict) else ""
        )
        if not outline:
            brief = json.dumps(raw, ensure_ascii=False)[:300] if raw else "(空响应)"
            raise RuntimeError(f"章纲生成未获得有效内容，大纲 API 响应: {brief}")
        return outline

    async def summarize_plot(self, history_text: str) -> str:
        """使用功能 api 总结剧情。"""
        # 传入后处理：剥离单章特有指示标记（#once N 及其后 N 行），防止其进入长期记忆
        history_text = strip_once_marks(history_text)
        if not history_text.strip():
            return ""
        client = self.get_func_client()
        if not client.is_configured():
            raise RuntimeError("剧情总结需要配置功能 api1/api2")
        raw = await call_with_retry(
            client.chat_json,
            [
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": history_text[:20000]},
            ],
            max_tokens=4096,
        )
        summary = (
            str(raw.get("summary", "")).strip() if isinstance(raw, dict) else ""
        )
        if not summary:
            brief = json.dumps(raw, ensure_ascii=False)[:300] if raw else "(空响应)"
            raise RuntimeError(
                f"剧情总结未获得有效 summary 字段，功能 API 响应: {brief}"
            )
        return summary

    async def generate_volume_outline(self, summary: str, requirement: str = "") -> str:
        """使用大纲 api1 生成卷纲。requirement 为可选的用户补充要求（如标题/章节数）。"""
        user_content = f"已总结的剧情：\n{summary}"
        if requirement and requirement.strip():
            user_content += f"\n\n用户对下一卷的补充要求：\n{requirement.strip()}"
        raw = await call_with_retry(
            self.outline_client1.chat_json,
            [
                {"role": "system", "content": VOLUME_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4096,
        )
        volume = (
            str(raw.get("volume_outline", "")).strip()
            if isinstance(raw, dict)
            else ""
        )
        if not volume:
            brief = json.dumps(raw, ensure_ascii=False)[:300] if raw else "(空响应)"
            raise RuntimeError(
                f"卷纲生成未获得有效 volume_outline 字段，大纲 API 响应: {brief}"
            )
        return volume

    # ------------------------------------------------------------------ #
    # 知识库更新
    # ------------------------------------------------------------------ #
    async def update_knowledge_command(
        self, summary: str
    ) -> list[dict[str, Any]]:
        """命令更新：根据剧情总结让功能 api 决定更新方式并返回命令列表。"""
        # 传入后处理：剥离单章特有指示标记
        summary = strip_once_marks(summary)
        client = self.get_func_client()
        dump = self.kb.dump_all_knowledge()
        payload = {
            "master_kb": self.kb.get_master_txt(),
            "knowledge": dump,
            "tool_commands": self.kb.get_fixed("tools"),
            "modify_requirements": self.kb.get_fixed("modify"),
            "plot_summary": summary,
        }
        raw = await call_with_retry(
            client.chat_json,
            [
                {"role": "system", "content": UPDATE_COMMAND_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False)[:40000],
                },
            ],
            max_tokens=8192,
        )
        updates = raw.get("updates", []) if isinstance(raw, dict) else []
        return updates if isinstance(updates, list) else []

    async def update_knowledge_per_chapter(
        self, injected_prompt: str, reply: str
    ) -> list[dict[str, Any]]:
        """逐章更新：将注入后的本对话 + AI 回复发给功能 api 判断是否需要更新。"""
        # 传入后处理：剥离单章特有指示标记
        injected_prompt = strip_once_marks(injected_prompt)
        reply = strip_once_marks(reply)
        client = self.get_func_client()
        payload = {
            "master_kb": self.kb.get_master_txt(),
            "tool_commands": self.kb.get_fixed("tools"),
            "modify_requirements": self.kb.get_fixed("modify"),
            "injected_prompt": injected_prompt[:15000],
            "ai_reply": reply[:15000],
        }
        raw = await call_with_retry(
            client.chat_json,
            [
                {"role": "system", "content": UPDATE_PER_CHAPTER_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False)[:40000],
                },
            ],
            max_tokens=8192,
        )
        if not isinstance(raw, dict):
            return []
        if not raw.get("need_update", False):
            return []
        updates = raw.get("updates", [])
        return updates if isinstance(updates, list) else []

    async def execute_updates(self, updates: list[dict[str, Any]]) -> list[str]:
        """执行知识库更新命令列表，返回执行日志。"""
        logs: list[str] = []
        for item in updates:
            if not isinstance(item, dict):
                continue
            cmd = item.get("command", "")
            args = item.get("args", {}) or {}
            try:
                await self._execute_one_update(cmd, args)
                logs.append(f"✓ {cmd} {json.dumps(args, ensure_ascii=False)[:120]}")
            except Exception as e:
                logs.append(f"✗ {cmd}: {e}")
                logger.error(f"[明阴全自动小说] 更新命令执行失败 {cmd}: {e}")
        return logs

    async def _execute_one_update(self, cmd: str, args: dict[str, Any]) -> None:
        kb = self.kb
        if cmd == "sqlite_insert":
            db, table, data = args.get("db"), args.get("table"), args.get("data")
            if not isinstance(data, dict):
                raise ValueError("sqlite_insert 需要 data 为 JSON 对象")
            kb.sqlite_insert(db, table, data)
        elif cmd == "sqlite_update":
            db, table, key, data = (
                args.get("db"),
                args.get("table"),
                args.get("key"),
                args.get("data"),
            )
            if not isinstance(data, dict):
                raise ValueError("sqlite_update 需要 data 为 JSON 对象")
            kb.sqlite_update(db, table, key, data)
        elif cmd == "sqlite_delete":
            kb.sqlite_delete(args.get("db"), args.get("table"), args.get("key"))
        elif cmd == "embedding_set":
            content = args.get("content", "")
            if not content:
                raise ValueError("embedding_set 需要 content")
            await kb.set_embedding_content(
                args.get("kb"), content, self.embed_texts
            )
        elif cmd == "embedding_add":
            content = args.get("content", "")
            if not content:
                raise ValueError("embedding_add 需要 content")
            await kb.add_embedding_content(
                args.get("kb"), content, self.embed_texts
            )
        elif cmd == "fixed_set":
            kb.set_fixed(args.get("name"), args.get("content", ""))
        else:
            raise ValueError(f"未知命令: {cmd}")

    # ------------------------------------------------------------------ #
    # 内容审核
    # ------------------------------------------------------------------ #
    async def audit_content(
        self, input_text: str, output_text: str
    ) -> dict[str, Any]:
        """对比输入与输出，返回 {"conflict": bool, "detail": str}。"""
        client = self.get_func_client()
        raw = await call_with_retry(
            client.chat_json,
            [
                {"role": "system", "content": AUDIT_SYSTEM},
                {
                    "role": "user",
                    "content": AUDIT_USER.format(
                        input_text=input_text[:12000],
                        output_text=output_text[:12000],
                    ),
                },
            ],
            max_tokens=1024,
        )
        if not isinstance(raw, dict):
            return {"conflict": False, "detail": ""}
        return {
            "conflict": bool(raw.get("conflict", False)),
            "detail": str(raw.get("detail", "")),
        }


def _cosine_score(a: list[float], b: list[float]) -> float:
    import math

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
