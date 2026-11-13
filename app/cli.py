"""命令行入口。

用法示例::

    python -m app.cli ~/Music/VipSongsDownload -f flac -o D:\\音乐
    python -m app.cli a.mflac b.mgg -f mp3 --bitrate 320
    python -m app.cli 目录/ -f alac --bits 24
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import formats  # noqa: E402
from core.formats import EncodeOptions  # noqa: E402
from core.pipeline import process  # noqa: E402
from platforms import base as platform_base  # noqa: E402


def collect(paths: list[str], recursive: bool = True) -> list[Path]:
    known = set(platform_base.addable_extensions())
    files: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            files.extend(
                f for f in sorted(p.glob(pattern))
                if f.is_file() and f.suffix.lower().lstrip(".") in known
            )
        elif p.is_file():
            files.append(p)
        else:
            print(f"[跳过] 路径不存在：{p}", file=sys.stderr)
    return files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music-unlock",
        description="把音乐平台的加密音频解成普通音频，并转成你想要的格式。",
    )
    parser.add_argument("paths", nargs="+", help="加密文件或目录")
    parser.add_argument(
        "-f", "--format", default=None, choices=[f.key for f in formats.ALL],
        help="输出格式（默认跟随源格式）",
    )
    parser.add_argument("-o", "--output", default="output", help="输出目录（默认 ./output）")
    parser.add_argument("--bitrate", type=int, default=None, help="有损格式码率 kbps")
    parser.add_argument("--bits", type=int, default=None, choices=[16, 24], help="无损格式位深")
    parser.add_argument("--rate", type=int, default=None, help="目标采样率")
    parser.add_argument("--name", default="{title} - {artist}", help="命名模板")
    parser.add_argument("--no-dither", action="store_true", help="关闭 24→16bit 抖动")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖同名文件")
    parser.add_argument("--dry-run", action="store_true", help="只列出要处理的文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    files = collect(args.paths)
    if not files:
        print("没有找到可处理的加密文件。", file=sys.stderr)
        print(f"当前支持的后缀：{', '.join(platform_base.addable_extensions())}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"将处理 {len(files)} 个文件：")
        for f in files:
            print("  ", f)
        return 0

    target = formats.get(args.format) if args.format else None
    opts = EncodeOptions(
        bit_depth=args.bits,
        sample_rate=args.rate,
        bitrate_kbps=args.bitrate,
        dither=not args.no_dither,
    )

    print(f"共 {len(files)} 个文件 -> 输出到 {Path(args.output).resolve()}")
    ok = failed = 0

    for index, src in enumerate(files, 1):
        prefix = f"[{index}/{len(files)}]"

        def on_stage(text: str, _p=prefix) -> None:
            print(f"{_p} {src.name} ... {text}", flush=True)

        last = [-1]

        def on_progress(value: float, _p=prefix) -> None:
            percent = int(value * 100)
            if percent // 10 != last[0]:
                last[0] = percent // 10
                print(f"{_p}   编码 {percent:3d}%", flush=True)

        result = process(
            src, args.output, target=target, opts=opts,
            naming=args.name, on_stage=on_stage, on_progress=on_progress,
            overwrite=args.overwrite,
        )

        if result.ok:
            ok += 1
            flag = "免重编码" if result.copied else f"{result.elapsed:.1f}s"
            print(f"{prefix} [OK] {result.output.name}  ({flag})")
            for warning in result.warnings:
                print(f"{prefix}   [!] {warning}")
        else:
            failed += 1
            print(f"{prefix} [FAIL] 失败：{result.message}", file=sys.stderr)

    print(f"\n完成：成功 {ok}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
