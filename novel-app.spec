# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：单 exe 内嵌 embedding（含 torch/sentence-transformers）
# 构建：python -m PyInstaller novel-app.spec --noconfirm --clean
from PyInstaller.utils.hooks import collect_submodules

_emb_hidden = (
    collect_submodules("sentence_transformers")
    + [
        m
        for m in collect_submodules("transformers")
        if not m.startswith("transformers.cli")
        and not m.startswith("transformers.benchmark")
    ]
    + collect_submodules("huggingface_hub")
    + collect_submodules("tokenizers")
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("webui", "webui"),
        ("plugins", "plugins"),
    ],
    hiddenimports=[
        "sqlite3",
        "_sqlite3",
        "torch",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
    ]
    + _emb_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "tensorflow",
        # transformers 的 CLI web 服务（serve）依赖 flask 且导入极慢/卡死，本项目用不到
        "transformers.cli",
        "transformers.benchmark",
        "flask",
        "tensorboard",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="novel_app",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 隐藏命令行
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="novel_app",
)
