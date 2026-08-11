"""明阴全自动小说 - 知识库存储管理模块。

数据目录结构：
    <data_dir>/
        master/master.db            # 主知识库（向量 + 文本）
        master/master_kb.txt        # 主知识库文本（供 LLM 模式判别）
        embedding/<name>.db         # embedding 知识库（向量 + 文本）
        embedding/<name>.txt        # embedding 知识库全文
        sqlite/<name>.db            # 用户自定义 sqlite 数据库
        fixed/writing_requirements.txt   # 写作要求固定文本
        fixed/current_volume_outline.txt# 当前卷纲固定文本
        fixed/modify_requirements.txt    # 预设修改要求固定文本
        fixed/tool_commands.txt          # 预设工具命令表
        output/                     # 导出对话 txt 输出目录
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import logging

logger = logging.getLogger("novel_app")

# 默认固定文本
DEFAULT_WRITING_REQUIREMENTS = (
    "这是小说创作，请保持剧情连贯、人物形象统一，注重细节描写和氛围渲染。\n"
    "本章内容应自然承接上文，不重复叙述已知信息。\n"
    "请以流畅的中文书写，避免网络用语和口语化表达。"
)
DEFAULT_MODIFY_REQUIREMENTS = (
    "角色表禁止删除角色；死亡角色仅将状态标记为\"死于xxx\"。\n"
    "历史线只追加、不篡改已发生的事件。\n"
    "embedding 背景设定库可以使用全文本修改命令整体更新。"
)
DEFAULT_TOOL_COMMANDS = """可用的知识库工具命令（仅使用以下命令，输出为 JSON 命令数组）：
1. sqlite_insert: 向 sqlite 表插入一行。参数: db, table, data(json)
2. sqlite_update: 更新 sqlite 表中主键匹配的一行。参数: db, table, key, data(json)
3. sqlite_delete: 删除 sqlite 表中主键匹配的一行。参数: db, table, key
4. embedding_set: 全文本修改一个 embedding 知识库的内容。参数: kb, content
5. embedding_add: 向 embedding 知识库追加内容。参数: kb, content
6. fixed_set: 修改固定文本。参数: name(writing|outline|modify), content
命令输出格式: [{"command": "sqlite_insert", "args": {...}}, ...]
"""

_VEC_FIELD = "vector_json"
_TEXT_FIELD = "text"


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """将长文本切分为有重叠的片段。"""
    text = (text or "").strip()
    if not text:
        return []
    # 先按空行分段，再合并超过 chunk_size 的长段
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(para), chunk_size - overlap):
                chunks.append(para[i : i + chunk_size])
        else:
            if len(buf) + len(para) + 1 > chunk_size and buf:
                chunks.append(buf)
                buf = ""
            buf = (buf + "\n" + para).strip() if buf else para
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c and c.strip()]

_ONCE_MARK_RE = re.compile(r"^\s*#once(?:\s+(\d+))?\s*$")


def strip_once_marks(text: str) -> str:
    """删除单章特有指示标记 `#once N` 及其后 N 行。

    标记必须独立成行（`#once 5` 独占一行），表示其后的 5 行属于
    单章特有指示，在知识库更新/剧情总结等长期记忆入口应被剥离。
    支持多个标记；`#once` 缺省行数时视为 1 行。

    Args:
        text: 原始文本。

    Returns:
        删除所有标记及其作用行后的文本。
    """
    if not text:
        return ""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _ONCE_MARK_RE.match(lines[i])
        if m:
            n = int(m.group(1)) if m.group(1) else 1
            i += 1 + n  # 跳过标记行 + 其后 N 行
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)



def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class KBStore:
    """知识库存储管理器。"""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.master_dir = self.data_dir / "master"
        self.embedding_dir = self.data_dir / "embedding"
        self.sqlite_dir = self.data_dir / "sqlite"
        self.fixed_dir = self.data_dir / "fixed"
        self.output_dir = self.data_dir / "output"
        self._lock = threading.RLock()
        for d in (
            self.master_dir,
            self.embedding_dir,
            self.sqlite_dir,
            self.fixed_dir,
            self.output_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

        self.master_db_path = self.master_dir / "master.db"
        self.master_txt_path = self.master_dir / "master_kb.txt"

    # ================================================================== #
    # 初始化
    # ================================================================== #
    def ensure_defaults(self) -> None:
        """确保默认知识库与固定文本存在。"""
        with self._lock:
            # 主知识库表
            conn = _connect(self.master_db_path)
            try:
                conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS master (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        desc TEXT DEFAULT '',
                        kind TEXT DEFAULT 'embedding',
                        {_VEC_FIELD} TEXT DEFAULT '[]'
                    )"""
                )
                conn.commit()
            finally:
                conn.close()

            # 默认 sqlite：角色表、历史线表
            self._ensure_default_sqlite()

            # 固定文本
            self._ensure_fixed(
                "writing_requirements.txt", DEFAULT_WRITING_REQUIREMENTS
            )
            self._ensure_fixed("current_volume_outline.txt", "")
            self._ensure_fixed("modify_requirements.txt", DEFAULT_MODIFY_REQUIREMENTS)
            self._ensure_fixed("tool_commands.txt", DEFAULT_TOOL_COMMANDS)

            self.refresh_master_txt()

    def _ensure_default_sqlite(self) -> None:
        # 角色表
        self.create_sqlite_db("角色", force=False)
        conn = _connect(self.sqlite_dir / "角色.db")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS 角色 (姓名 TEXT PRIMARY KEY, 人设 TEXT, 状态 TEXT)"
            )
            conn.commit()
        finally:
            conn.close()
        self.add_master_entry("角色", "角色数据表（姓名、人设、状态）", "sqlite")

        # 历史线表
        self.create_sqlite_db("历史线", force=False)
        conn = _connect(self.sqlite_dir / "历史线.db")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS 历史线 (时间 TEXT, 事件 TEXT, 备注 TEXT)"
            )
            conn.commit()
        finally:
            conn.close()
        self.add_master_entry("历史线", "剧情时间线记录", "sqlite")

        # 背景设定 embedding 知识库
        self.create_embedding_kb("背景设定", "世界背景、势力、地域、设定等", force=False)

    # ================================================================== #
    # 主知识库
    # ================================================================== #
    def _master_entries(self) -> list[dict[str, Any]]:
        conn = _connect(self.master_db_path)
        try:
            rows = conn.execute("SELECT name, desc, kind, vector_json FROM master").fetchall()
            out = []
            for name, desc, kind, vec in rows:
                out.append(
                    {
                        "name": name,
                        "desc": desc or "",
                        "kind": kind or "embedding",
                        "vector": json.loads(vec or "[]"),
                    }
                )
            return out
        finally:
            conn.close()

    def add_master_entry(
        self, name: str, desc: str, kind: str, vector: list[float] | None = None
    ) -> None:
        with self._lock:
            conn = _connect(self.master_db_path)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO master (name, desc, kind, vector_json) VALUES (?, ?, ?, ?)",
                    (name, desc, kind, json.dumps(vector or [], ensure_ascii=False)),
                )
                conn.commit()
            finally:
                conn.close()
            self.refresh_master_txt()

    def update_master_vector(self, name: str, vector: list[float]) -> None:
        with self._lock:
            conn = _connect(self.master_db_path)
            try:
                conn.execute(
                    "UPDATE master SET vector_json=? WHERE name=?",
                    (json.dumps(vector, ensure_ascii=False), name),
                )
                conn.commit()
            finally:
                conn.close()

    def remove_master_entry(self, name: str) -> None:
        with self._lock:
            conn = _connect(self.master_db_path)
            try:
                conn.execute("DELETE FROM master WHERE name=?", (name,))
                conn.commit()
            finally:
                conn.close()
            self.refresh_master_txt()

    def refresh_master_txt(self) -> None:
        """把主知识库所有条目写成文本文件（供 LLM 模式 / 更新使用）。"""
        lines = ["# 知识库列表", "# 格式: 类型: 识别名: 描述"]
        for e in self._master_entries():
            lines.append(f"{e['kind']}: {e['name']}: {e['desc']}")
        self.master_txt_path.write_text(
            "\n".join(lines), encoding="utf-8", errors="replace"
        )

    def get_master_txt(self) -> str:
        return self.master_txt_path.read_text(encoding="utf-8", errors="replace")

    # ================================================================== #
    # embedding 知识库
    # ================================================================== #
    def create_embedding_kb(self, name: str, desc: str, force: bool = True) -> None:
        if not re.match(r"^[\w\u4e00-\u9fff-]{1,50}$", name or ""):
            raise ValueError("知识库名称只能包含中英文、数字、下划线和短横线，且不超过 50 字符")
        db_path = self.embedding_dir / f"{name}.db"
        exists = db_path.exists()
        if exists and not force:
            return
        with self._lock:
            conn = _connect(db_path)
            try:
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    f"{_TEXT_FIELD} TEXT, {_VEC_FIELD} TEXT)"
                )
                conn.commit()
            finally:
                conn.close()
            txt_path = self.embedding_dir / f"{name}.txt"
            if not txt_path.exists():
                txt_path.write_text("", encoding="utf-8")
            self.add_master_entry(name, desc, "embedding")

    def _embedding_kb_names(self) -> list[str]:
        return [p.stem for p in self.embedding_dir.glob("*.db")]

    def embedding_kb_text(self, name: str) -> str:
        txt_path = self.embedding_dir / f"{name}.txt"
        if txt_path.exists():
            return txt_path.read_text(encoding="utf-8", errors="replace")
        return ""

    async def add_embedding_content(self, name: str, content: str, embed_func) -> int:
        """向 embedding 知识库追加内容并计算向量。

        embed_func: async callable (texts: list[str]) -> list[list[float]]
        返回新增片段数量。
        """
        db_path = self.embedding_dir / f"{name}.db"
        if not db_path.exists():
            raise ValueError(f"embedding 知识库不存在: {name}")
        chunks = chunk_text(content)
        if not chunks:
            return 0
        vectors = await embed_func(chunks)
        added = 0
        with self._lock:
            conn = _connect(db_path)
            try:
                for text, vec in zip(chunks, vectors):
                    conn.execute(
                        f"INSERT INTO chunks ({_TEXT_FIELD}, {_VEC_FIELD}) VALUES (?, ?)",
                        (text, json.dumps(vec, ensure_ascii=False)),
                    )
                    added += 1
                conn.commit()
            finally:
                conn.close()
            txt_path = self.embedding_dir / f"{name}.txt"
            with open(txt_path, "a", encoding="utf-8") as f:
                f.write(("\n" if txt_path.exists() and txt_path.stat().st_size else "") + content)
        logger.info(f"[明阴全自动小说] embedding 知识库 {name} 新增 {added} 个片段")
        return added

    async def set_embedding_content(self, name: str, content: str, embed_func) -> int:
        """全文本修改一个 embedding 知识库（清空后重建）。"""
        db_path = self.embedding_dir / f"{name}.db"
        if not db_path.exists():
            raise ValueError(f"embedding 知识库不存在: {name}")
        chunks = chunk_text(content)
        vectors = await embed_func(chunks) if chunks else []
        with self._lock:
            conn = _connect(db_path)
            try:
                conn.execute("DELETE FROM chunks")
                for text, vec in zip(chunks, vectors):
                    conn.execute(
                        f"INSERT INTO chunks ({_TEXT_FIELD}, {_VEC_FIELD}) VALUES (?, ?)",
                        (text, json.dumps(vec, ensure_ascii=False)),
                    )
                conn.commit()
            finally:
                conn.close()
            (self.embedding_dir / f"{name}.txt").write_text(
                content, encoding="utf-8", errors="replace"
            )
        logger.info(f"[明阴全自动小说] embedding 知识库 {name} 已全文本修改（{len(chunks)} 片段）")
        return len(chunks)

    def search_embedding_kb(
        self, name: str, query_vector: list[float], top_k: int = 3
    ) -> list[dict[str, Any]]:
        """在指定 embedding 知识库中检索与 query_vector 最相似的片段。"""
        db_path = self.embedding_dir / f"{name}.db"
        if not db_path.exists():
            return []
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"SELECT {_TEXT_FIELD}, {_VEC_FIELD} FROM chunks"
            ).fetchall()
        finally:
            conn.close()
        scored: list[tuple[float, str]] = []
        for text, vec in rows:
            try:
                vec_data = json.loads(vec or "[]")
            except Exception:
                continue
            sim = _cosine(query_vector, vec_data)
            if sim > 0:
                scored.append((sim, text))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"text": t, "score": round(s, 4)}
            for s, t in scored[: max(1, int(top_k))]
        ]

    # ================================================================== #
    # sqlite 数据库
    # ================================================================== #
    def create_sqlite_db(self, name: str, force: bool = True) -> None:
        if not re.match(r"^[\w\u4e00-\u9fff-]{1,50}$", name or ""):
            raise ValueError("数据库名称只能包含中英文、数字、下划线和短横线，且不超过 50 字符")
        db_path = self.sqlite_dir / f"{name}.db"
        if db_path.exists() and not force:
            return
        with self._lock:
            _connect(db_path).close()
            self.add_master_entry(name, f"sqlite 数据库 {name}", "sqlite")

    def _sqlite_conn(self, name: str) -> sqlite3.Connection:
        db_path = self.sqlite_dir / f"{name}.db"
        if not db_path.exists():
            raise ValueError(f"sqlite 数据库不存在: {name}")
        return _connect(db_path)

    def _sanitize_identifier(self, ident: str) -> str:
        if not re.match(r"^[\w\u4e00-\u9fff]{1,50}$", ident or ""):
            raise ValueError(f"非法的表/字段名: {ident!r}")
        return ident

    def create_sqlite_table(
        self, db_name: str, table: str, columns: dict[str, str]
    ) -> None:
        """创建 sqlite 表。columns: {列名: 类型}，第一个列作为主键。"""
        table = self._sanitize_identifier(table)
        if not columns:
            raise ValueError("至少需要指定一个字段")
        col_defs = []
        for i, (cname, ctype) in enumerate(columns.items()):
            cname = self._sanitize_identifier(cname)
            ctype = (ctype or "TEXT").upper()
            if i == 0:
                col_defs.append(f'"{cname}" {ctype} PRIMARY KEY')
            else:
                col_defs.append(f'"{cname}" {ctype}')
        conn = self._sqlite_conn(db_name)
        try:
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(col_defs)})')
            conn.commit()
        finally:
            conn.close()

    def list_sqlite_tables(self, db_name: str) -> list[str]:
        conn = self._sqlite_conn(db_name)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def sqlite_insert(self, db_name: str, table: str, data: dict[str, Any]) -> None:
        table = self._sanitize_identifier(table)
        if not data:
            raise ValueError("data 不能为空")
        cols = list(data.keys())
        for c in cols:
            self._sanitize_identifier(c)
        placeholders = ", ".join(["?"] * len(cols))
        quoted = ", ".join(f'"{c}"' for c in cols)
        conn = self._sqlite_conn(db_name)
        try:
            conn.execute(
                f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({placeholders})',
                [data[c] for c in cols],
            )
            conn.commit()
        finally:
            conn.close()

    def sqlite_update(
        self, db_name: str, table: str, key: Any, data: dict[str, Any]
    ) -> None:
        table = self._sanitize_identifier(table)
        conn = self._sqlite_conn(db_name)
        try:
            pk = self._primary_key(conn, table)
            if not pk:
                raise ValueError(f"表 {table} 没有主键，无法更新")
            if pk not in data:
                data[pk] = key
            sets = ", ".join(f'"{self._sanitize_identifier(c)}"=?' for c in data)
            conn.execute(
                f'UPDATE "{table}" SET {sets} WHERE "{pk}"=?',
                [data[c] for c in data] + [key],
            )
            conn.commit()
        finally:
            conn.close()

    def sqlite_delete(self, db_name: str, table: str, key: Any) -> None:
        table = self._sanitize_identifier(table)
        conn = self._sqlite_conn(db_name)
        try:
            pk = self._primary_key(conn, table)
            if not pk:
                raise ValueError(f"表 {table} 没有主键，无法删除")
            conn.execute(f'DELETE FROM "{table}" WHERE "{pk}"=?', (key,))
            conn.commit()
        finally:
            conn.close()

    def sqlite_select_all(
        self, db_name: str, table: str
    ) -> list[dict[str, Any]]:
        table = self._sanitize_identifier(table)
        conn = self._sqlite_conn(db_name)
        try:
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            cols = [
                d[0]
                for d in conn.execute(f'SELECT * FROM "{table}"').description
            ]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            conn.close()

    def _primary_key(self, conn: sqlite3.Connection, table: str) -> str | None:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        for r in rows:
            if r[5] == 1:  # pk
                return r[1]
        return rows[0][1] if rows else None

    def match_sqlite_by_keys(
        self, corpus: str, top_dbs: list[str] | None = None
    ) -> list[str]:
        """拿每个 sqlite 表的主键去 corpus 中匹配，命中则注入整行记录。

        返回注入文本片段列表。
        """
        results: list[str] = []
        names = self._sqlite_names() if top_dbs is None else top_dbs
        for name in names:
            try:
                tables = self.list_sqlite_tables(name)
            except Exception:
                continue
            for table in tables:
                conn = self._sqlite_conn(name)
                try:
                    pk = self._primary_key(conn, table)
                    if not pk:
                        continue
                    rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
                    cols = [
                        d[0]
                        for d in conn.execute(
                            f'SELECT * FROM "{table}"'
                        ).description
                    ]
                finally:
                    conn.close()
                for row in rows:
                    record = dict(zip(cols, row))
                    key_val = record.get(pk)
                    if key_val is None:
                        continue
                    # 主键值出现在文本中即命中
                    key_str = str(key_val)
                    if key_str and key_str in corpus:
                        line = "，".join(
                            f"{c}:{v}"
                            for c, v in record.items()
                            if v not in (None, "")
                        )
                        results.append(f"【{name}·{table}】{line}")
        return results

    def _sqlite_names(self) -> list[str]:
        return [p.stem for p in self.sqlite_dir.glob("*.db")]

    def _entry_kind(self, name: str) -> str:
        """查询某个知识库识别名的类型（embedding/sqlite），未知返回空。"""
        for e in self._master_entries():
            if e["name"] == name:
                return e["kind"]
        return ""


    # ================================================================== #
    # 固定文本
    # ================================================================== #
    _FIXED_FILES = {
        "writing": "writing_requirements.txt",
        "outline": "current_volume_outline.txt",
        "modify": "modify_requirements.txt",
        "tools": "tool_commands.txt",
    }

    def _ensure_fixed(self, filename: str, content: str) -> None:
        path = self.fixed_dir / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    def get_fixed(self, key: str) -> str:
        name = self._FIXED_FILES.get(key)
        if not name:
            return ""
        path = self.fixed_dir / name
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def set_fixed(self, key: str, content: str) -> None:
        name = self._FIXED_FILES.get(key)
        if not name:
            raise ValueError(f"未知固定文本类型: {key}")
        (self.fixed_dir / name).write_text(content, encoding="utf-8", errors="replace")

    # ================================================================== #
    # 汇总 / 导出
    # ================================================================== #
    def list_all_knowledge(self) -> list[dict[str, Any]]:
        """返回所有知识库信息（含主知识库条目）。"""
        entries = self._master_entries()
        out = []
        for e in entries:
            kind = e["kind"]
            count = 0
            if kind == "embedding":
                try:
                    conn = _connect(self.embedding_dir / f"{e['name']}.db")
                    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                    conn.close()
                except Exception:
                    count = 0
            elif kind == "sqlite":
                try:
                    for t in self.list_sqlite_tables(e["name"]):
                        conn = self._sqlite_conn(e["name"])
                        count += conn.execute(
                            f'SELECT COUNT(*) FROM "{t}"'
                        ).fetchone()[0]
                        conn.close()
                except Exception:
                    count = 0
            out.append(
                {
                    "name": e["name"],
                    "desc": e["desc"],
                    "kind": kind,
                    "count": count,
                }
            )
        return out

    def save_conversation_export(self, text: str) -> str:
        """把对话内容写入 output 目录，返回文件路径。"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"对话导出_{timestamp}.txt"
        path.write_text(text, encoding="utf-8", errors="replace")
        return str(path)

    def export_all_knowledge_text(self) -> str:
        """以文本模式汇总所有知识库内容（主知识库/sqlite/固定文本/embedding）。

        Returns:
            结构化的完整导出文本。
        """
        parts: list[str] = []
        parts.append("=" * 50)
        parts.append("明阴全自动小说 · 全知识库文本导出")
        parts.append(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        parts.append("=" * 50)

        # 主知识库
        parts.append("\n【一、主知识库】")
        parts.append(self.get_master_txt())

        # 固定文本
        parts.append("\n【二、固定文本】")
        fixed_names = {
            "writing": "写作要求",
            "outline": "当前卷纲",
            "modify": "预设修改要求",
            "tools": "预设工具命令表",
        }
        for key, title in fixed_names.items():
            content = self.get_fixed(key).strip()
            parts.append(f"\n-- {title} --")
            parts.append(content if content else "(空)")

        # sqlite 库
        parts.append("\n【三、sqlite 数据库】")
        sqlite_names = self._sqlite_names()
        if not sqlite_names:
            parts.append("(无)")
        for db in sqlite_names:
            parts.append(f"\n-- 数据库: {db} --")
            try:
                tables = self.list_sqlite_tables(db)
            except Exception as e:
                parts.append(f"  (读取失败: {e})")
                continue
            for t in tables:
                parts.append(f"\n  表: {t}")
                try:
                    rows = self.sqlite_select_all(db, t)
                except Exception as e:
                    parts.append(f"    (读取失败: {e})")
                    continue
                if not rows:
                    parts.append("    (空)")
                for r in rows:
                    line = "，".join(
                        f"{k}:{v}" for k, v in r.items() if v not in (None, "")
                    )
                    parts.append(f"    {line}")

        # embedding 知识库
        parts.append("\n【四、embedding 知识库】")
        emb_names = self._embedding_kb_names()
        if not emb_names:
            parts.append("(无)")
        for kb_name in emb_names:
            parts.append(f"\n-- 知识库: {kb_name} --")
            content = self.embedding_kb_text(kb_name).strip()
            parts.append(content if content else "(空)")

        return "\n".join(parts)

    def save_knowledge_export(self, text: str) -> str:
        """把全知识库导出写入 output 下以当时时间命名的文件夹，返回文件夹路径。"""
        folder = self.output_dir / f"知识库导出_{time.strftime('%Y%m%d_%H%M%S')}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "知识库导出.txt"
        path.write_text(text, encoding="utf-8", errors="replace")
        return str(folder)

    def dump_all_knowledge(self) -> dict[str, Any]:
        """导出所有知识库内容（供知识库更新流程使用）。"""
        dump: dict[str, Any] = {"embedding_kbs": {}, "sqlite_dbs": {}}
        for e in self._master_entries():
            if e["kind"] == "embedding":
                dump["embedding_kbs"][e["name"]] = self.embedding_kb_text(e["name"])
            elif e["kind"] == "sqlite":
                tables: dict[str, Any] = {}
                for t in self.list_sqlite_tables(e["name"]):
                    tables[t] = self.sqlite_select_all(e["name"], t)
                dump["sqlite_dbs"][e["name"]] = tables
        return dump

