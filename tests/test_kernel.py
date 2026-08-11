"""插件化架构测试（mock 生成端与 novel 业务层，不调用真实 API）。"""

import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.kernel import NovelKernel


class FakeGenerator:
    def __init__(self):
        self.calls = []
        self.histories = []

    async def generate(self, ctx):
        self.calls.append(ctx.prompt)
        self.histories.append(ctx.history)
        return "AI 回复"


class FakeNovel:
    def __init__(self):
        self.calls = {
            "build_background": 0,
            "audit_content": 0,
            "generate_volume_outline": [],
        }

    async def build_background(self, conv):
        self.calls["build_background"] += 1
        return "写作背景\n# 测试背景\n内容"

    async def build_chapter_outline(self, text):
        return "章纲内容"

    async def audit_content(self, input_text, output_text):
        self.calls["audit_content"] += 1
        return {"conflict": False, "detail": ""}

    async def update_knowledge_per_chapter(self, injected, reply):
        return []

    async def execute_updates(self, updates):
        return []

    async def embed_texts(self, texts):
        return [[0.1] * 8 for _ in texts]

    async def summarize_plot(self, history_text):
        return "剧情总结"

    async def generate_volume_outline(self, summary, requirement=""):
        self.calls["generate_volume_outline"].append(requirement)
        return "第一卷《大小圣女》，约十章"


class TestKernel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="novel_kernel_test_")
        self.kernel = NovelKernel({}, self.tmp)
        self.kernel.generator = FakeGenerator()
        self.fake_novel = FakeNovel()
        for p in self.kernel.plugin_manager.plugins:
            if p.name == "novel":
                p.novel_for_session = lambda s: self.fake_novel

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plugins_loaded(self):
        names = [p.name for p in self.kernel.plugin_manager.plugins]
        self.assertIn("cmd_dispatch", names)
        self.assertIn("history", names)
        self.assertIn("novel", names)
        priorities = [p.priority for p in self.kernel.plugin_manager.plugins]
        self.assertEqual(priorities, sorted(priorities))
        table = self.kernel.plugin_manager.get_table()
        self.assertTrue(any(p["name"] == "novel" for p in table["plugins"]))

    def test_setting_commands_aggregated(self):
        keys = {i["key"] for i in self.kernel.get_setting_commands()}
        self.assertIn("history_count", keys)
        self.assertIn("retrieve_mode", keys)
        self.assertIn("content_audit", keys)

    def test_mock_no_real_api(self):
        """验证测试全程走 mock（FakeNovel/FakeGenerator），不产生真实 API 调用。"""
        async def _run():
            await self.kernel.handle_message("sm", "继续写")
            self.assertEqual(self.fake_novel.calls["build_background"], 1)
            self.assertEqual(len(self.kernel.generator.calls), 1)
            # 生成端确实是 FakeGenerator（返回固定文本）
            self.assertEqual(
                await self.kernel.handle_message("sm2", "再写"),
                "AI 回复",
            )

        asyncio.run(_run())

    def test_message_roundtrip(self):
        async def _run():
            reply = await self.kernel.handle_message("s1", "继续写")
            self.assertEqual(reply, "AI 回复")
            session = self.kernel.chat_store.load("s1")
            self.assertEqual(len(session.messages), 2)
            self.assertIn("写作背景", self.kernel.generator.calls[0])

        asyncio.run(_run())

    def test_cmd_dispatch(self):
        async def _run():
            reply = await self.kernel.handle_message("s2", "//help")
            self.assertIn("明阴全自动小说", reply)
            self.assertEqual(self.kernel.generator.calls, [])

        asyncio.run(_run())

    def test_history_format(self):
        async def _run():
            await self.kernel.handle_message("s3", "第一句")
            await self.kernel.handle_message("s3", "第二句")
            history = self.kernel.generator.histories[-1]
            self.assertIn("#####", history)
            self.assertIn("#USER:第一句", history)
            self.assertIn("#AI:AI 回复", history)

        asyncio.run(_run())

    def test_retry_last_assistant(self):
        async def _run():
            await self.kernel.handle_message("s4", "写一章")
            await self.kernel.retry_last_assistant("s4")
            session = self.kernel.chat_store.load("s4")
            self.assertEqual(len(session.messages), 2)
            self.assertEqual(len(self.kernel.generator.calls), 2)

        asyncio.run(_run())

    def test_session_setting(self):
        async def _run():
            await self.kernel.handle_message("s5", "//temp 1.2")
            session = self.kernel.chat_store.load("s5")
            self.assertAlmostEqual(session.temperature, 1.2)
            await self.kernel.handle_message("s5", "//name 我的小说")
            session = self.kernel.chat_store.load("s5")
            self.assertEqual(session.name, "我的小说")

        asyncio.run(_run())

    def test_kb_commands(self):
        async def _run():
            r = await self.kernel.handle_message("s6", "//kb list")
            self.assertIn("角色", r)
            r = await self.kernel.handle_message("s6", "//kb create 设定 世界观")
            self.assertIn("已创建", r)
            r = await self.kernel.handle_message("s6", "//sqlite create 装备")
            self.assertIn("已创建", r)
            r = await self.kernel.handle_message(
                "s6", "//sqlite table 装备 装备 名称:TEXT,属性:TEXT"
            )
            self.assertIn("已创建", r)
            r = await self.kernel.handle_message(
                "s6", "//sqlite insert 装备 装备 {\"名称\":\"剑\",\"属性\":\"锋利\"}"
            )
            self.assertIn("已插入", r)
            r = await self.kernel.handle_message("s6", "//sqlite show 装备 装备")
            self.assertIn("剑", r)
            r = await self.kernel.handle_message("s6", "//fixed outline 第一卷：觉醒")
            self.assertIn("已更新", r)

        asyncio.run(_run())

    def test_multi_kbset(self):
        async def _run():
            await self.kernel.handle_message("s7", "//kbset 第二部")
            r = await self.kernel.handle_message("s7", "//kb create 第二部设定 独立")
            self.assertIn("已创建", r)
            r = await self.kernel.handle_message("s7", "//kb list")
            self.assertIn("第二部设定", r)

        asyncio.run(_run())

    def test_outline_update_with_requirement(self):
        """//outline update 支持补充要求：换行形式 / 同行形式 / 无要求 / 非法。"""
        async def _run():
            # 先产生剧情历史
            await self.kernel.handle_message("so", "开始写第一章")
            # 换行形式（用户实际用法）
            r = await self.kernel.handle_message(
                "so",
                "//outline update\n第一卷的标题应为《大小圣女》，约十章",
            )
            self.assertIn("已更新", r)
            reqs = self.fake_novel.calls["generate_volume_outline"]
            self.assertEqual(reqs[-1], "第一卷的标题应为《大小圣女》，约十章")
            # 同行形式
            r = await self.kernel.handle_message("so", "//outline update 标题《大小圣女》")
            self.assertIn("已更新", r)
            self.assertEqual(reqs[-1], "标题《大小圣女》")
            # 无要求
            r = await self.kernel.handle_message("so", "//outline update")
            self.assertIn("已更新", r)
            self.assertEqual(reqs[-1], "")
            # 非法子命令
            r = await self.kernel.handle_message("so", "//outline xxx")
            self.assertIn("用法", r)

        asyncio.run(_run())

    def test_reload_plugins(self):
        """新插件置入 → 重载 → 插件表与设置命令表自动更新。"""
        pdir = Path(tempfile.mkdtemp(prefix="novel_plugin_dir_"))
        demo = pdir / "demo"
        demo.mkdir()
        (demo / "plugin.py").write_text(
            "from core.plugin_base import Plugin\n"
            "class DemoPlugin(Plugin):\n"
            "    name = 'demo'\n"
            "    priority = 99\n"
            "    command_table = [{'key': 'demo_flag', 'label': '演示开关', "
            "'type': 'bool', 'default': False}]\n"
            "    async def before_generate(self, ctx):\n"
            "        ctx.extras['demo_ran'] = True\n",
            encoding="utf-8",
        )
        try:
            kernel = NovelKernel({}, self.tmp, plugins_dir=pdir)
            names = [p.name for p in kernel.plugin_manager.plugins]
            self.assertIn("demo", names)
            keys = {i["key"] for i in kernel.get_setting_commands()}
            self.assertIn("demo_flag", keys)
            table = kernel.plugin_manager.get_table()
            self.assertTrue(any(p["name"] == "demo" for p in table["plugins"]))
        finally:
            shutil.rmtree(pdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

