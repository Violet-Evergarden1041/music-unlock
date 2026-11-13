"""定位 ffmpeg / ffprobe 可执行文件。

查找顺序：
1. 打包后的 bin/ 目录（PyInstaller onedir / onefile 解包目录）
2. 项目根目录 bin/
3. 项目根目录 vendor/ffmpeg/bin/
4. 系统 PATH
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 上隐藏子进程黑窗
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_EXE_SUFFIX = ".exe" if os.name == "nt" else ""


class FFmpegNotFound(RuntimeError):
    """找不到 ffmpeg，且无法自动修复。"""


def _search_roots() -> list[Path]:
    roots: list[Path] = []

    if getattr(sys, "frozen", False):
        # PyInstaller：exe 同级 + 解包临时目录
        roots.append(Path(sys.executable).resolve().parent / "bin")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass) / "bin")

    project_root = Path(__file__).resolve().parent.parent
    roots.append(project_root / "bin")
    roots.append(project_root / "vendor" / "ffmpeg" / "bin")
    return roots


def find(name: str) -> str:
    """返回 ffmpeg/ffprobe 的绝对路径，找不到抛异常。"""
    exe = name + _EXE_SUFFIX
    for root in _search_roots():
        candidate = root / exe
        if candidate.is_file():
            return str(candidate)

    found = shutil.which(name)
    if found:
        return found

    raise FFmpegNotFound(
        f"未找到 {name}。请把它放到 {_search_roots()[-1].parent} 目录，"
        f"或运行 `python -m app.setup --fetch-ffmpeg` 自动下载。"
    )


def ffmpeg() -> str:
    return find("ffmpeg")


def ffprobe() -> str:
    return find("ffprobe")


def run_quiet(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """跑一个不弹窗、不阻塞的后台命令。"""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def version(exe: str) -> str:
    """取一句版本号，例如 'ffmpeg version 7.1 ...'。"""
    try:
        out = run_quiet([exe, "-version"], timeout=20)
        first = out.stdout.splitlines()[0] if out.stdout else ""
        return first.strip()
    except Exception as exc:  # pragma: no cover - 仅诊断用
        return f"<无法执行: {exc}>"
