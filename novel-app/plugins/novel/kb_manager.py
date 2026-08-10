"""多套知识库管理：每一套知识库使用一个独立文件夹。"""

from __future__ import annotations

import logging
from pathlib import Path

from .kb_store import KBStore

logger = logging.getLogger("novel_app")


class KBManager:
    """管理 kbs/<套名>/ 下的多套知识库。"""

    def __init__(self, data_root: str | Path) -> None:
        self.kbs_root = Path(data_root) / "kbs"
        self.kbs_root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, KBStore] = {}

    def list_sets(self) -> list[str]:
        """列出所有知识库套。"""
        names = sorted(d.name for d in self.kbs_root.iterdir() if d.is_dir())
        return names

    def create_set(self, name: str) -> KBStore:
        """创建（或复用）一套知识库。"""
        store = self.get_store(name)
        store.ensure_defaults()
        return store

    def get_store(self, name: str | None = None) -> KBStore:
        """获取指定套的知识库存储；缺省为 default 套。"""
        name = (name or "default").strip()
        if name not in self._cache:
            store = KBStore(self.kbs_root / name)
            store.ensure_defaults()
            self._cache[name] = store
        return self._cache[name]

    def rename_set(self, old: str, new: str) -> None:
        self._cache.pop(old, None)
        (self.kbs_root / old).rename(self.kbs_root / new)

    def delete_set(self, name: str) -> None:
        self._cache.pop(name, None)
        target = self.kbs_root / name
        if target.exists():
            import shutil

            shutil.rmtree(target, ignore_errors=True)
