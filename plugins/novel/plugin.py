"""小说插件：知识库管理、背景注入、章纲、内容审核、逐章更新、复杂命令。

依赖：core.api_client（工具库）、同目录 kb_store/novel_service/kb_manager。
"""

from __future__ import annotations

import json
import logging

from core.plugin_base import Plugin, PluginContext
from plugins.novel.kb_manager import KBManager
from plugins.novel.novel_service import NovelService

logger = logging.getLogger("novel_app")


class NovelPlugin(Plugin):
    name = "novel"
    priority = 20
    description = "嵌套知识库 / 背景注入 / 章纲 / 审核 / 逐章更新"
    command_table = [
        {
            "key": "retrieve_mode",
            "label": "知识库判别模式",
            "type": "select",
            "options": ["embedding", "llm"],
            "default": "embedding",
            "hint": "embedding=内嵌向量判别；llm=功能 api 判别",
        },
        {
            "key": "full_scan",
            "label": "每章检索全部知识库",
            "type": "bool",
            "default": False,
            "hint": "开启后跳过主知识库判别，检索所有知识库",
        },
        {
            "key": "multi_func_api_mode",
            "label": "多功能 API 模式",
            "type": "bool",
            "default": False,
            "hint": "开启后审核/判别使用功能 api2",
        },
        {
            "key": "multi_outline_api_mode",
            "label": "多大纲 API 模式",
            "type": "bool",
            "default": False,
            "hint": "开启后章纲使用大纲 api2",
        },
        {
            "key": "multi_llm_union",
            "label": "多 API 联合",
            "type": "bool",
            "default": False,
            "hint": "开启后章纲由大纲 API 生成",
        },
        {
            "key": "content_audit",
            "label": "内容审核",
            "type": "bool",
            "default": False,
            "hint": "输出与输入对比审核，冲突时回复首行打标签",
        },
        {
            "key": "auto_update_mode",
            "label": "知识库自动更新模式",
            "type": "select",
            "options": ["off", "per_chapter", "command"],
            "default": "command",
            "hint": "off=关闭；per_chapter=逐章；command=命令",
        },
        {
            "key": "kbset",
            "label": "知识库套",
            "type": "string",
            "default": "default",
            "hint": "当前对话使用的知识库套名",
        },
    ]

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self.kb_manager = KBManager(kernel.data_root)

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def kb_for_session(self, session) -> "KBStore":
        return self.kb_manager.get_store(
            session.settings.get("kbset") or "default"
        )

    def novel_for_session(self, session) -> NovelService:
        return NovelService(self.kernel.config, self.kb_for_session(session))

    def session_history_text(self, session) -> str:
        lines = []
        for m in session.messages:
            label = "用户" if m.role == "user" else "AI"
            lines.append(f"{label}: {m.content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 工作流钩子
    # ------------------------------------------------------------------ #
    async def before_generate(self, ctx: PluginContext) -> None:
        kb = self.kb_for_session(ctx.session)
        novel = self.novel_for_session(ctx.session)
        ctx.extras["kb"] = kb
        ctx.extras["novel"] = novel
        ctx.extras["system_prompt"] = kb.get_fixed("writing").strip()

        # 背景注入
        conv = f"{ctx.history}\n{ctx.user_text}" if ctx.history else ctx.user_text
        try:
            ctx.background = await novel.build_background(conv)
        except Exception as e:
            logger.error(f"背景注入失败: {e}")
        if ctx.background.strip():
            ctx.prompt = f"{ctx.background}\n\n{ctx.user_text}"

        # 章纲（多 API 联合）
        if ctx.session.settings.get("multi_llm_union", False):
            try:
                outline = await novel.build_chapter_outline(
                    f"{ctx.background}\n\n{ctx.user_text}"
                )
            except Exception as e:
                logger.error(f"章纲生成失败: {e}")
                return
            if outline:
                ctx.prompt = (
                    f"{ctx.background}\n\n续写大纲\n{outline}\n请基于此展开本章内容\n\n"
                    f"{ctx.user_text}"
                )

    async def after_generate(self, ctx: PluginContext) -> None:
        if ctx.consume:
            return
        novel = ctx.extras.get("novel")
        if novel is None:
            return
        # 内容审核
        if ctx.session.settings.get("content_audit", False):
            try:
                audit = await novel.audit_content(
                    f"{ctx.background}\n\n{ctx.user_text}", ctx.reply
                )
            except Exception as e:
                logger.error(f"内容审核失败: {e}")
            else:
                if audit.get("conflict"):
                    detail = str(audit.get("detail", "")).strip()
                    tag = f"⚠️ 存在冲突标签：{detail}" if detail else "⚠️ 存在冲突标签"
                    ctx.reply = tag + "\n" + ctx.reply
        # 逐章更新
        if ctx.session.settings.get("auto_update_mode") == "per_chapter":
            import asyncio

            injected = f"{ctx.background}\n\n{ctx.user_text}"
            asyncio.create_task(
                self._per_chapter_update(novel, injected, ctx.reply)
            )

    async def _per_chapter_update(self, novel, injected: str, reply: str) -> None:
        try:
            updates = await novel.update_knowledge_per_chapter(injected, reply)
            if updates:
                logs = await novel.execute_updates(updates)
                logger.info(f"逐章更新完成: {'; '.join(logs)}")
        except Exception as e:
            logger.error(f"逐章更新失败: {e}")

    # ------------------------------------------------------------------ #
    # 复杂命令（//xxx，由 cmd_dispatch 插件路由到各插件 handle_command）
    # ------------------------------------------------------------------ #
    async def handle_command(self, session, cmd: str, arg: str) -> str | None:
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler is None:
            return None
        try:
            return await handler(session, arg)
        except Exception as e:
            logger.error(f"命令 //{cmd} 执行失败: {e}")
            return f"❌ 命令执行失败: {e}"

    async def _cmd_kb(self, session, arg: str) -> str:
        kb = self.kb_for_session(session)
        parts = arg.split(None, 2)
        sub = parts[0].lower() if parts else ""
        name = parts[1] if len(parts) > 1 else ""
        content = parts[2] if len(parts) > 2 else ""

        if sub == "list":
            lines = ["📚 知识库列表:"]
            for e in kb.list_all_knowledge():
                lines.append(
                    f"- [{e['kind']}] {e['name']}（{e['desc']}）条目/行数: {e['count']}"
                )
            return "\n".join(lines)
        if sub == "create":
            if not name:
                return "用法: //kb create <名称> <描述>"
            try:
                kb.create_embedding_kb(name, content or "")
                return f"✅ embedding 知识库已创建: {name}"
            except Exception as e:
                return f"❌ 创建失败: {e}"
        if sub == "add":
            if not name or not content:
                return "用法: //kb add <名称> <内容>"
            try:
                novel = self.novel_for_session(session)
                n = await kb.add_embedding_content(name, content, novel.embed_texts)
                return f"✅ 已向 {name} 追加 {n} 个片段"
            except Exception as e:
                return f"❌ 追加失败: {e}"
        if sub == "set":
            if not name or not content:
                return "用法: //kb set <名称> <内容>"
            try:
                novel = self.novel_for_session(session)
                n = await kb.set_embedding_content(name, content, novel.embed_texts)
                return f"✅ 已全文本修改 {name}（{n} 个片段）"
            except Exception as e:
                return f"❌ 修改失败: {e}"
        if sub == "remove":
            if not name:
                return "用法: //kb remove <名称>"
            try:
                kb.remove_master_entry(name)
                for p in (
                    kb.embedding_dir / f"{name}.db",
                    kb.embedding_dir / f"{name}.txt",
                    kb.sqlite_dir / f"{name}.db",
                ):
                    if p.exists():
                        p.unlink()
                return f"✅ 已删除知识库: {name}"
            except Exception as e:
                return f"❌ 删除失败: {e}"
        return "用法: //kb list|create|add|set|remove"

    async def _cmd_sqlite(self, session, arg: str) -> str:
        kb = self.kb_for_session(session)
        parts = arg.split(None, 4)
        sub = parts[0].lower() if parts else ""

        if sub == "create":
            db = parts[1] if len(parts) > 1 else ""
            if not db:
                return "用法: //sqlite create <库名>"
            try:
                kb.create_sqlite_db(db)
                return f"✅ sqlite 库已创建: {db}"
            except Exception as e:
                return f"❌ 创建失败: {e}"
        if sub == "table":
            db = parts[1] if len(parts) > 1 else ""
            table = parts[2] if len(parts) > 2 else ""
            spec = parts[3] if len(parts) > 3 else ""
            if not db or not table or not spec:
                return "用法: //sqlite table <库名> <表名> <字段:类型,...>"
            try:
                columns: dict[str, str] = {}
                for item in spec.split(","):
                    item = item.strip()
                    if ":" in item:
                        cname, ctype = item.split(":", 1)
                        columns[cname.strip()] = ctype.strip() or "TEXT"
                    elif item:
                        columns[item] = "TEXT"
                kb.create_sqlite_table(db, table, columns)
                return f"✅ 表已创建: {db}.{table}"
            except Exception as e:
                return f"❌ 建表失败: {e}"
        if sub == "insert":
            iparts = arg.split(None, 3)
            db = iparts[1] if len(iparts) > 1 else ""
            table = iparts[2] if len(iparts) > 2 else ""
            data_s = iparts[3] if len(iparts) > 3 else ""
            if not db or not table or not data_s:
                return "用法: //sqlite insert <库名> <表名> <json>"
            try:
                data = json.loads(data_s)
                if not isinstance(data, dict):
                    raise ValueError("需要 JSON 对象")
                kb.sqlite_insert(db, table, data)
                return f"✅ 已插入 {db}.{table}"
            except Exception as e:
                return f"❌ 插入失败: {e}"
        if sub == "update":
            if len(parts) < 5:
                return "用法: //sqlite update <库名> <表名> <主键> <json>"
            db, table, key, data_s = parts[1], parts[2], parts[3], parts[4]
            try:
                data = json.loads(data_s)
                if not isinstance(data, dict):
                    raise ValueError("需要 JSON 对象")
                kb.sqlite_update(db, table, key, data)
                return f"✅ 已更新 {db}.{table}（主键 {key}）"
            except Exception as e:
                return f"❌ 更新失败: {e}"
        if sub == "delete":
            if len(parts) < 4:
                return "用法: //sqlite delete <库名> <表名> <主键>"
            db, table, key = parts[1], parts[2], parts[3]
            try:
                kb.sqlite_delete(db, table, key)
                return f"✅ 已删除 {db}.{table}（主键 {key}）"
            except Exception as e:
                return f"❌ 删除失败: {e}"
        if sub == "show":
            db = parts[1] if len(parts) > 1 else ""
            table = parts[2] if len(parts) > 2 else ""
            if not db:
                return "用法: //sqlite show <库名> [表名]"
            try:
                tables = [table] if table else kb.list_sqlite_tables(db)
                lines = [f"📦 {db}:"]
                for t in tables:
                    rows = kb.sqlite_select_all(db, t)
                    lines.append(f"  - {t}（{len(rows)} 行）:")
                    for r in rows[:50]:
                        lines.append(f"    {r}")
                    if len(rows) > 50:
                        lines.append(f"    ... 共 {len(rows)} 行")
                return "\n".join(lines)
            except Exception as e:
                return f"❌ 查询失败: {e}"
        return "用法: //sqlite create|table|insert|update|delete|show"

    async def _cmd_fixed(self, session, arg: str) -> str:
        kb = self.kb_for_session(session)
        parts = arg.split(None, 1)
        key = parts[0].lower() if parts else ""
        content = parts[1] if len(parts) > 1 else ""
        if key not in ("writing", "outline", "modify"):
            return "用法: //fixed <writing|outline|modify> <内容>"
        if not content:
            current = kb.get_fixed(key)
            return f"当前 {key} 固定文本：\n{current or '(空)'}"
        try:
            kb.set_fixed(key, content)
            return f"✅ 固定文本 {key} 已更新"
        except Exception as e:
            return f"❌ 修改失败: {e}"

    async def _cmd_kbset(self, session, arg: str) -> str:
        if not arg.strip():
            return "用法: //kbset <套名>"
        name = arg.strip()
        self.kb_manager.create_set(name)
        session.settings["kbset"] = name
        self.kernel.chat_store.save(session)
        return f"✅ 已切换知识库套: {name}"

    async def _cmd_kbset_list(self, session, arg: str) -> str:
        sets = self.kb_manager.list_sets()
        return "📦 知识库套: " + (", ".join(sets) if sets else "(无)")

    async def _cmd_update(self, session, arg: str) -> str:
        """命令更新：功能 api1 总结剧情 -> 决定并执行知识库更新。"""
        try:
            history = self.session_history_text(session)
            if not history.strip():
                return "❌ 当前会话没有可用的剧情内容。"
            novel = self.novel_for_session(session)
            summary = await novel.summarize_plot(history)
            if not summary:
                return "❌ 剧情总结失败：功能 API 未返回有效 summary，请检查功能 API 配置。"
            updates = await novel.update_knowledge_command(summary)
            if not updates:
                return "ℹ️ 功能 API 判定无需更新知识库。"
            logs = await novel.execute_updates(updates)
            lines = ["✅ 命令更新完成："]
            lines.extend(f"  {log}" for log in logs)
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"命令更新失败: {e}")
            return f"❌ 命令更新失败: {e}"

    async def _cmd_outline(self, session, arg: str) -> str:
        """更新分卷：总结剧情 -> 大纲 api1 生成卷纲 -> 更新当前卷纲固定文本。

        用法：
          //outline update                  仅基于剧情总结生成卷纲
          //outline update <补充要求>       生成卷纲时参考要求（如标题、章节数）
        """
        raw = (arg or "").strip()
        if not raw:
            return "用法: //outline update [补充要求]"
        lines = raw.splitlines()
        head = lines[0].strip()
        if not head.lower().startswith("update"):
            return "用法: //outline update [补充要求]"
        # 补充要求：首行 "update" 之后的内容（同行），加上后续所有行
        requirement = head[6:].strip()
        if len(lines) > 1:
            tail = "\n".join(lines[1:]).strip()
            requirement = (requirement + "\n" + tail).strip() if requirement else tail
        try:
            history = self.session_history_text(session)
            if not history.strip():
                return "❌ 当前会话没有可用的剧情内容。"
            novel = self.novel_for_session(session)
            summary = await novel.summarize_plot(history)
            if not summary:
                return "❌ 剧情总结失败：功能 API 未返回有效 summary，请检查功能 API 配置。"
            volume_outline = await novel.generate_volume_outline(summary, requirement)
            if not volume_outline:
                return "❌ 卷纲生成失败：大纲 API 未返回有效卷纲内容，请检查大纲 api1 配置。"
            self.kb_for_session(session).set_fixed("outline", volume_outline)
            return f"✅ 已更新当前卷纲固定文本：\n\n{volume_outline}"
        except Exception as e:
            logger.error(f"更新分卷失败: {e}")
            return f"❌ 更新分卷失败: {e}"

    async def _cmd_export(self, session, arg: str) -> str:
        try:
            history = self.session_history_text(session)
            if not history.strip():
                return "❌ 当前会话没有可导出的内容。"
            kb = self.kb_for_session(session)
            header = (
                "明阴全自动小说 · 对话导出\n"
                f"会话: {session.session_id}\n"
                + "=" * 40
                + "\n"
            )
            path = kb.save_conversation_export(header + history)
            return f"✅ 已导出到: {path}"
        except Exception as e:
            logger.error(f"导出失败: {e}")
            return f"❌ 导出失败: {e}"

    async def _cmd_export_kb(self, session, arg: str) -> str:
        try:
            kb = self.kb_for_session(session)
            text = kb.export_all_knowledge_text()
            folder = kb.save_knowledge_export(text)
            return f"✅ 全知识库已导出到文件夹:\n{folder}"
        except Exception as e:
            logger.error(f"知识库导出失败: {e}")
            return f"❌ 知识库导出失败: {e}"
