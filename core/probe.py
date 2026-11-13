"""用 ffprobe 读取音频的真实参数。

转码前必须先知道：源 codec、采样率、位深、声道、时长、有没有封面。
这些决定了「能不能免重编码」以及「要不要抖动/重采样」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import ffbin

#: 这些 codec 算无损，用来判断「有损转无损」的无意义操作
LOSSLESS_CODECS = frozenset({
    "flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_u8",
    "pcm_s16be", "pcm_s24be", "pcm_s32be", "pcm_f32le", "pcm_f64le",
    "wavpack", "ape", "truehd", "mlp", "tta",
})

#: sample_fmt 前缀 → 位深
_FMT_BITS = {
    "u8": 8, "s16": 16, "s32": 32, "flt": 32, "dbl": 64,
}


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: Path
    container: str = ""
    codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    bits: int = 0
    duration: float = 0.0
    bit_rate: int = 0
    has_cover: bool = False
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def lossless(self) -> bool:
        return self.codec in LOSSLESS_CODECS

    @property
    def duration_hms(self) -> str:
        total = int(round(self.duration))
        return f"{total // 60}:{total % 60:02d}"

    @property
    def bits_per_sample_label(self) -> str:
        # 有损编码没有“位深”这个概念，报 32bit 只会让人困惑
        if not self.lossless or not self.bits:
            return "—"
        return f"{self.bits}bit"

    def describe(self) -> str:
        return (
            f"{self.codec} {self.sample_rate / 1000:.1f}kHz "
            f"{self.bits_per_sample_label} {self.channels}ch"
        )


def _bits_from_stream(stream: dict) -> int:
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        raw = stream.get(key)
        if raw:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    sample_fmt = str(stream.get("sample_fmt") or "")
    for prefix, bits in _FMT_BITS.items():
        if sample_fmt.startswith(prefix):
            return bits
    return 16


def _clean_tags(raw: dict | None) -> dict[str, str]:
    if not raw:
        return {}
    return {str(k).lower(): str(v) for k, v in raw.items() if v is not None}


def probe(path: str | Path) -> MediaInfo:
    """读一个音频文件，失败抛 ProbeError。"""
    path = Path(path)
    if not path.is_file():
        raise ProbeError(f"文件不存在：{path}")

    cmd = [
        ffbin.ffprobe(),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = ffbin.run_quiet(cmd, timeout=120)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise ProbeError(f"ffprobe 解析失败：{err[-1] if err else proc.returncode}")

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe 输出不是合法 JSON：{exc}") from exc

    streams = data.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise ProbeError("这个文件里没有音频流")

    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or audio.get("duration") or 0.0)

    cover = any(
        s.get("codec_type") == "video"
        and (s.get("disposition") or {}).get("attached_pic") == 1
        for s in streams
    )

    tags = _clean_tags(fmt.get("tags"))
    for key, value in _clean_tags(audio.get("tags")).items():
        tags.setdefault(key, value)

    codec = str(audio.get("codec_name") or "")
    return MediaInfo(
        path=path,
        container=str(fmt.get("format_name") or ""),
        codec=codec,
        sample_rate=int(audio.get("sample_rate") or 0),
        channels=int(audio.get("channels") or 0),
        bits=_bits_from_stream(audio) if codec in LOSSLESS_CODECS else 0,
        duration=duration,
        bit_rate=int(fmt.get("bit_rate") or audio.get("bit_rate") or 0),
        has_cover=cover,
        tags=tags,
    )


def quick_duration(path: str | Path) -> float:
    """只取时长，用于算进度百分比；失败返回 0。"""
    try:
        return probe(path).duration
    except Exception:
        return 0.0
