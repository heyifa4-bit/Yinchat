"""插件管理器：扫描插件文件夹、加载/卸载插件、维护插件表、聚合命令表。

插件目录约定：plugins/<插件名>/plugin.py（入口，定义 Plugin 子类）。
重载时：新插件置入 → reload() → 插件表与设置命令表自动更新。
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .plugin_base import Plugin

logger = logging.getLogger("novel_app")

TABLE_FILE = "plugin_table.json"


class PluginManager:
    def __init__(self, kernel, plugins_dir: str | Path) -> None:
        self.kernel = kernel
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.plugins: list[Plugin] = []  # 已加载实例（按 priority 升序）

    # ------------------------------------------------------------------ #
    # 重载
    # ------------------------------------------------------------------ #
    def reload(self) -> dict[str, list[str]]:
        """扫描插件文件夹并重载。

        新增插件 → 加载；移除插件 → 卸载；变化 → 重建。
        返回 {"loaded": [...], "removed": [...], "failed": [...]}
        """
        loaded: list[Plugin] = []
        failed: list[str] = []
        seen_dirs: dict[str, str] = {}  # dir_name -> 模块标识

        for d in sorted(self.plugins_dir.iterdir()):
            if not d.is_dir():
                continue
            entry = d / "plugin.py"
            if not entry.exists():
                continue
            try:
                inst = self._load_from_dir(d)
                loaded.append(inst)
                seen_dirs[inst.name] = d.name
            except Exception as e:
                failed.append(f"{d.name}: {e}")
                logger.error(f"插件加载失败 [{d.name}]: {e}")

        old = {p.name: p for p in self.plugins}
        new_names = {p.name for p in loaded}

        # 卸载已移除的插件
        for name, p in old.items():
            if name not in new_names:
                try:
                    p.on_unload()
                except Exception:
                    logger.exception(f"插件卸载异常 [{name}]")

        self.plugins = loaded
        self.plugins.sort(key=lambda p: p.priority)
        self._dir_map = seen_dirs
        self._write_table()

        return {
            "loaded": [p.name for p in loaded],
            "removed": sorted(set(old) - new_names),
            "failed": failed,
        }

    def _load_from_dir(self, plugin_dir: Path) -> Plugin:
        """从插件目录加载 plugin.py 并实例化 Plugin 子类。"""
        entry = plugin_dir / "plugin.py"
        module_name = f"novel_plugin_{plugin_dir.name}"
        # 若旧模块已加载，先移除，避免重复注册
        for k in [k for k in sys.modules if k == module_name or k.startswith(module_name + ".")]:
            sys.modules.pop(k, None)
        spec = importlib.util.spec_from_file_location(module_name, entry)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载 plugin.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        cls: type[Plugin] | None = None
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Plugin) and obj is not Plugin:
                cls = obj
                break
        if cls is None:
            raise RuntimeError("plugin.py 中未找到 Plugin 子类")
        inst = cls(self.kernel)
        if not inst.name:
            raise RuntimeError("插件 name 不能为空")
        inst._dir_name = plugin_dir.name
        inst.on_load()
        return inst

    # ------------------------------------------------------------------ #
    # 插件表
    # ------------------------------------------------------------------ #
    def _write_table(self) -> None:
        table: dict[str, Any] = {"schema_version": 1, "plugins": []}
        for p in self.plugins:
            table["plugins"].append(
                {
                    "name": p.name,
                    "path": f"{p._dir_name}/plugin.py",
                    "priority": p.priority,
                    "enabled": True,
                    "version": p.version,
                    "description": p.description,
                }
            )
        (self.plugins_dir / TABLE_FILE).write_text(
            json.dumps(table, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_table(self) -> dict:
        path = self.plugins_dir / TABLE_FILE
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                pass
        return {"schema_version": 1, "plugins": []}

    # ------------------------------------------------------------------ #
    # 命令 / 设置聚合
    # ------------------------------------------------------------------ #
    def get_setting_commands(self) -> list[dict[str, Any]]:
        """聚合所有插件的简单开关命令表（重载后调用）。"""
        merged: list[dict[str, Any]] = []
        for p in self.plugins:
            for item in p.command_table or []:
                item = dict(item)
                item["plugin"] = p.name
                merged.append(item)
        return merged

    async def dispatch_command(self, session, cmd: str, arg: str) -> str | None:
        """依次让各插件尝试处理复杂命令。"""
        for p in self.plugins:
            result = await p.handle_command(session, cmd, arg)
            if result is not None:
                return result
        return None
