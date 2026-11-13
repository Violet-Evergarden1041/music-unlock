"""环境自检与一键修复。

用法::

    python -m app.setup            # 体检
    python -m app.setup --fetch-ffmpeg   # 下载 ffmpeg 到 bin/
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import ffbin  # noqa: E402
from platforms import base as platform_base  # noqa: E402

FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
    "ffmpeg-master-latest-win64-gpl.zip"
)


def fetch_ffmpeg(dest: Path | None = None) -> Path:
    """下载并解出 ffmpeg.exe / ffprobe.exe 到 bin/。"""
    dest = dest or ROOT / "bin"
    dest.mkdir(parents=True, exist_ok=True)

    print(f"下载 ffmpeg（约 200MB）…\n  {FFMPEG_URL}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        last = [0]

        def hook(block: int, size: int, total: int) -> None:
            if total <= 0:
                return
            done = block * size / 1048576
            if done - last[0] >= 10:
                last[0] = done
                print(f"  {done:6.1f} / {total / 1048576:.0f} MB", flush=True)

        urllib.request.urlretrieve(FFMPEG_URL, archive, hook)
        print("解压…")
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                name = Path(member).name
                if name in ("ffmpeg.exe", "ffprobe.exe"):
                    with zf.open(member) as src, open(dest / name, "wb") as out:
                        shutil.copyfileobj(src, out)

    for name in ("ffmpeg.exe", "ffprobe.exe"):
        target = dest / name
        if not target.is_file():
            raise RuntimeError(f"压缩包里没找到 {name}")
        print(f"  ✔ {target}  {target.stat().st_size / 1048576:.0f} MB")
    return dest


def doctor() -> int:
    print("=== 环境检查 ===")
    problems = 0

    print(f"Python      {sys.version.split()[0]}")

    for name in ("ffmpeg", "ffprobe"):
        try:
            path = ffbin.find(name)
            print(f"{name:11} ✔ {path}")
            print(f"            {ffbin.version(path)}")
        except ffbin.FFmpegNotFound:
            print(f"{name:11} ✘ 缺失")
            problems += 1

    try:
        import PySide6

        print(f"PySide6     ✔ {PySide6.__version__}")
    except ImportError:
        print("PySide6     ✘ 未安装（图形界面不可用，命令行仍可用）")
        problems += 1

    exts = platform_base.known_extensions()
    print(f"\n已注册解密器 {len(platform_base.REGISTRY)} 个，支持 {len(exts)} 种后缀")
    for decoder in platform_base.REGISTRY:
        print(f"  · {decoder.name}: {', '.join(decoder.extensions)}")

    if problems:
        print(f"\n发现 {problems} 个问题。")
        if shutil.which("ffmpeg") is None:
            print("修复 ffmpeg：python -m app.setup --fetch-ffmpeg")
        if problems:
            print("修复界面依赖：pip install -r requirements.txt")
        return 1

    print("\n一切就绪。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.setup", description="环境自检 / 修复")
    parser.add_argument("--fetch-ffmpeg", action="store_true", help="下载 ffmpeg 到 bin/")
    args = parser.parse_args(argv)

    if args.fetch_ffmpeg:
        fetch_ffmpeg()
        print("完成。")
        return 0
    return doctor()


if __name__ == "__main__":
    raise SystemExit(main())
