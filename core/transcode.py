"""调用 ffmpeg 做实际编码，带进度回调与取消支持。

只处理明文音频。加密文件的解密在 platforms/ 层，跟这里无关。
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from . import ffbin
from .formats import AudioFormat, EncodeOptions, build_args, can_copy, warnings_for
from .probe import MediaInfo

ProgressFn = Callable[[float], None]


@dataclass
class TranscodeResult:
    ok: bool
    output: Path
    message: str = ""
    stderr_tail: str = ""
    copied: bool = False
    warnings: list[str] = field(default_factory=list)


def _cover_args(info: MediaInfo, fmt: AudioFormat) -> list[str]:
    """封面：支持的格式就原样搬过去，不支持的显式丢掉。"""
    if info.has_cover and fmt.supports_cover:
        return ["-map", "0:v:0?", "-c:v", "copy"]
    return ["-vn"]


def build_command(
    src: Path,
    dst: Path,
    info: MediaInfo,
    fmt: AudioFormat,
    opts: EncodeOptions,
    meta: Mapping[str, str] | None = None,
    cover_path: Path | None = None,
) -> tuple[list[str], bool]:
    """拼出完整 ffmpeg 命令，同时返回是否走了免重编码。"""
    copy = can_copy(fmt, info.codec, info.sample_rate, info.bits, opts)
    codec_args = build_args(
        fmt,
        opts,
        src_codec=info.codec,
        src_sample_rate=info.sample_rate,
        src_bits=info.bits,
        copy=copy,
    )

    cmd = [
        ffbin.ffmpeg(),
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", str(src),
    ]

    # 封面：解密器带出来的图（网易云 ncm）优先，其次用文件里原有的
    if cover_path is not None and fmt.supports_cover:
        cmd += [
            "-i", str(cover_path),
            "-map", "0:a:0",
            "-map", "1:v:0",
            "-c:v", "copy",
            "-disposition:v", "attached_pic",
        ]
    else:
        cmd += ["-map", "0:a:0", *_cover_args(info, fmt)]

    cmd += ["-map_metadata", "0"]

    if meta:
        # 解密层带来的元数据（例如网易云 ncm 头部信息）优先于容器自带标签
        for key, value in meta.items():
            if value:
                cmd += ["-metadata", f"{key}={value}"]

    cmd += codec_args
    cmd.append(str(dst))
    return cmd, copy


def _run_ffmpeg(
    cmd: list[str],
    total: float,
    progress: ProgressFn | None,
    cancel: threading.Event | None,
    timeout: int,
) -> tuple[int, str, bool]:
    """跑一条 ffmpeg 命令，返回 (退出码, stderr, 是否被取消)。"""
    with tempfile.TemporaryFile() as errfile:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=errfile,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=ffbin.CREATE_NO_WINDOW,
        )

        cancelled = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    cancelled = True
                    break
                line = line.strip()
                if not line.startswith("out_time_us="):
                    continue
                raw = line.split("=", 1)[1]
                if raw in ("N/A", "") or not progress or total <= 0:
                    continue
                try:
                    seconds = int(raw) / 1_000_000
                except ValueError:
                    continue
                progress(max(0.0, min(0.999, seconds / total)))
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return -1, "ffmpeg 超时", False

        errfile.seek(0)
        stderr_text = errfile.read().decode("utf-8", "replace").strip()

    return code, stderr_text, cancelled


def transcode(
    src: str | Path,
    dst: str | Path,
    info: MediaInfo,
    fmt: AudioFormat,
    opts: EncodeOptions | None = None,
    *,
    meta: Mapping[str, str] | None = None,
    cover_path: str | Path | None = None,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    timeout: int = 3600,
) -> TranscodeResult:
    """把一个明文音频转成目标格式。"""
    src, dst = Path(src), Path(dst)
    opts = opts or EncodeOptions()
    dst.parent.mkdir(parents=True, exist_ok=True)

    cover = Path(cover_path) if cover_path is not None else None
    cmd, copied = build_command(src, dst, info, fmt, opts, meta, cover)
    warns = warnings_for(fmt, info.codec)
    total = info.duration or 0.0

    code, stderr_text, cancelled = _run_ffmpeg(cmd, total, progress, cancel, timeout)

    # 兜底：某些容器/编码器组合收不了封面（实测 ogg 系会直接拒），
    # 去掉封面重试一次，而不是让用户对着一条 ffmpeg 报错发懵。
    if code != 0 and cover is not None and fmt.supports_cover and not cancelled:
        warns.append(f"{fmt.label} 无法内嵌封面，已跳过封面重试")
        cmd, _ = build_command(src, dst, info, fmt, opts, meta, None)
        code, stderr_text, cancelled = _run_ffmpeg(
            cmd, total, progress, cancel, timeout
        )

    if cancelled:
        _safe_unlink(dst)
        return TranscodeResult(False, dst, "已取消", warnings=warns)

    if code == -1:
        return TranscodeResult(False, dst, "ffmpeg 超时", warnings=warns)

    if code != 0:
        tail = "\n".join(stderr_text.splitlines()[-6:])
        return TranscodeResult(
            False, dst, f"ffmpeg 失败（退出码 {code}）", stderr_tail=tail, warnings=warns
        )

    if not dst.is_file() or dst.stat().st_size == 0:
        return TranscodeResult(
            False, dst, "ffmpeg 报告成功但没有产出文件",
            stderr_tail=stderr_text[-500:], warnings=warns,
        )

    if progress:
        progress(1.0)

    return TranscodeResult(
        True, dst, "免重编码（直接封装）" if copied else "已转码",
        copied=copied, warnings=warns,
    )


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
