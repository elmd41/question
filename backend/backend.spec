# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 配置文件 - 打包 Python FastAPI 后端为单文件 exe

使用方法:
    cd backend
    pyinstaller backend.spec

输出:
    dist/backend.exe
"""

import os
import sys
from pathlib import Path

# 项目根目录
project_root = Path(SPECPATH).parent
backend_dir = project_root / "backend"

a = Analysis(
    [str(backend_dir / "entry.py")],
    pathex=[str(backend_dir)],
    binaries=[],
    datas=[
        # 包含 app 模块（必需，否则找不到模块）
        (str(backend_dir / "app"), "app"),
        # 包含前端构建产物（如果有）
        (str(project_root / "frontend" / "dist"), "frontend/dist"),
    ],
    hiddenimports=[
        # FastAPI 相关
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # WebSockets
        "websockets",
        "websockets.legacy",
        "websockets.legacy.server",
        # Pydantic
        "pydantic",
        # Argon2
        "argon2_cffi",
        # HTTPX
        "httpx",
        "h11",
        "h2",
        "hpack",
        "hyperframe",
        # 其他
        "encodings.idna",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
        "test",
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # 使用 UPX 压缩（需要安装 UPX）
    upx_exclude=[],
    runtime_tmpdir=None,  # 使用系统临时目录
    console=True,  # 显示控制台窗口，方便调试
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "build" / "icon.ico") if (project_root / "build" / "icon.ico").exists() else None,
)
