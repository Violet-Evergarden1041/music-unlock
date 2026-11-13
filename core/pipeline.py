"""单文件流水线：识别 → 解密 → 探测 → 编码决策 → 转码 → 校验。

上层（CLI / GUI）只需要调用 ``process()``，不必知道任何一个平台或格式的细节。
"""

from __future__ import annotations

import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import formats
from .formats import AudioFormat, EncodeOptions
from .probe import MediaInfo, ProbeError, probe
from .transcode import TranscodeResult, transcode

StageFn = Callable[[str], None]
ProgressFn = Callable[[float], None]

#: Windows 文件名非法字符
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
#: QQ 音乐下载文件名把分隔符换成了下划线
_SEP = " - "


@dataclass
class JobResult:
    ok: bool
    src: Path
    output: Path | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    info: MediaInfo | None = None
    copied: bool = False
    elapsed: float = 0.0


# --------------------------------------------------------------------------- #
# 命名
# --------------------------------------------------------------------------- #


def _spill(data: bytes, container: str, root: Path) -> Path:
    """把解密出来的明文写到临时文件，交给 ffprobe / ffmpeg。"""
    with tempfile.NamedTemporaryFile(
        dir=root, suffix=f".{container}", delete=False
    ) as handle:
        handle.write(data)
        return Path(handle.name)


def sanitize(name: str, max_len: int = 120) -> str:
    name = _ILLEGAL.sub("_", name).strip().rstrip(".")
    return name[:max_len] or "untitled"


def parse_filename(stem: str, artist_first: bool = False) -> dict[str, str]:
    """把文件名拆成歌名和歌手。

    QQ 音乐下载的是 ``歌名 - 歌手``；酷狗 / 酷我是 ``歌手 - 歌名``。
    """
    if _SEP in stem:
        left, right = stem.split(_SEP, 1)
        if artist_first:
            return {"title": right.strip(), "artist": left.strip()}
        return {"title": left.strip(), "artist": right.strip()}
    return {"title": stem.strip(), "artist": ""}


def build_name(
    template: str,
    meta: dict[str, str],
    fallback_stem: str,
    ext: str,
    artist_first: bool = False,
) -> str:
    values = {"title": "", "artist": "", "album": ""}
    values.update(parse_filename(fallback_stem, artist_first))
    values.update({k: v for k, v in meta.items() if k in values})
    try:
        rendered = template.format(**values)
    except (KeyError, IndexError):
        rendered = template
    rendered = re.sub(r"\s*-\s*$", "", rendered)  # 歌手为空时去掉尾部的 " -"
    return f"{sanitize(rendered)}.{ext}"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def process(
    src: str | Path,
    out_dir: str | Path,
    *,
    target: AudioFormat | str | None = None,
    opts: EncodeOptions | None = None,
    naming: str = "{title} - {artist}",
    on_stage: StageFn | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    temp_dir: str | Path | None = None,
    overwrite: bool = False,
) -> JobResult:
    """处理一个文件。任何异常都被收敛成 JobResult，不往上层抛。"""
    import time

    from platforms import base as platform_base

    started = time.perf_counter()
    src = Path(src)
    out_dir = Path(out_dir)

    def stage(text: str) -> None:
        if on_stage:
            on_stage(text)

    if isinstance(target, str):
        target = formats.get(target)

    try:
        tmp_root = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
        tmp_root.mkdir(parents=True, exist_ok=True)

        # 1. 识别：加密文件交给对应解密器，普通音频直接透传
        decoder = platform_base.decoder_for(src)
        if decoder is not None:
            stage(f"解密（{decoder.name}）")
            decoded = decoder.decode(src)
            media_path: Path | None = _spill(decoded.data, decoded.container, tmp_root)
            meta = decoded.meta
            notes = list(decoded.notes)
            cover_bytes = decoded.cover
        elif platform_base.is_plain_audio(src):
            stage("普通音频，跳过解密")
            decoded = None
            media_path = None          # 直接用源文件，不复制
            meta = {}
            notes = []
            cover_bytes = None
        else:
            return JobResult(False, src, message=f"不支持的格式：{src.suffix or '(无扩展名)'}")

        probe_src = media_path or src
        cover_path: Path | None = None
        try:
            if cover_bytes:
                stage("提取内嵌封面")
                suffix = ".jpg" if cover_bytes[:3] == b"\xff\xd8\xff" else ".png"
                with tempfile.NamedTemporaryFile(
                    dir=tmp_root, suffix=suffix, delete=False
                ) as handle:
                    handle.write(cover_bytes)
                    cover_path = Path(handle.name)

            stage("探测源参数")
            info = probe(probe_src)
            if info.duration <= 0:
                raise ProbeError("解出来的音频时长为 0，可能密钥不对")

            fmt = target or formats.default_for_source(info.codec)
            stage(f"转码 → {fmt.label}")
            out_dir.mkdir(parents=True, exist_ok=True)
            # 平台元数据优先于容器自带标签：ncm 里的信息才是权威的
            merged = {**(info.tags or {}), **meta}
            name = build_name(
                naming, merged, src.stem, fmt.ext,
                artist_first=platform_base.filename_order(src) == "artist-title",
            )
            dst = out_dir / name
            if not overwrite:
                dst = unique_path(dst)

            result: TranscodeResult = transcode(
                probe_src, dst, info, fmt, opts or EncodeOptions(),
                meta=meta or None,
                cover_path=cover_path,
                progress=on_progress, cancel=cancel,
            )
        finally:
            if media_path is not None:
                media_path.unlink(missing_ok=True)
            if cover_path is not None:
                cover_path.unlink(missing_ok=True)

        if not result.ok:
            return JobResult(
                False, src, message=result.message,
                warnings=result.warnings, info=info, elapsed=time.perf_counter() - started,
            )

        # 3. 校验产物
        stage("校验产物")
        warnings = [*notes, *result.warnings]
        try:
            out_info = probe(result.output)
            if info.duration and abs(out_info.duration - info.duration) > 1.0:
                warnings.append(
                    f"时长不一致（源 {info.duration:.1f}s / 产物 {out_info.duration:.1f}s）"
                )
        except ProbeError as exc:
            warnings.append(f"产物复检失败：{exc}")

        return JobResult(
            True, src, result.output, result.message,
            warnings=warnings, info=info, copied=result.copied,
            elapsed=time.perf_counter() - started,
        )

    except platform_base.DecryptError as exc:
        return JobResult(False, src, message=str(exc), elapsed=time.perf_counter() - started)
    except ProbeError as exc:
        return JobResult(False, src, message=str(exc), elapsed=time.perf_counter() - started)
    except Exception as exc:  # 兜底：单个文件失败不能拖垮整批
        return JobResult(
            False, src, message=f"{type(exc).__name__}: {exc}",
            elapsed=time.perf_counter() - started,
        )
