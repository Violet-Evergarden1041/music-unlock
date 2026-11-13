"""输出格式矩阵。

这一层只做一件事：把「用户选的格式 + 质量选项」翻译成 ffmpeg 参数。
它不认识任何加密格式，也不碰文件 IO。

设计要点
--------
* 源格式 == 目标格式时走 ``-c:a copy``，绝不二次重编码；
* 位深默认跟随源（24bit 母带不会被悄悄降到 16bit）；
* 降位深自动加抖动，避免量化失真；
* 有损 → 无损会给出明确警告，而不是默默生成一个更大的有损文件。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# --------------------------------------------------------------------------- #
# 选项
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EncodeOptions:
    """一次编码的质量选项。None 表示「跟随源 / 用该格式的默认值」。"""

    bit_depth: int | None = None          # 16 / 24，仅无损格式有效
    sample_rate: int | None = None        # None = 跟随源
    bitrate_kbps: int | None = None       # 有损格式的固定码率
    compression_level: int | None = None  # FLAC 0-12
    vorbis_quality: int | None = None     # OGG -q:a 0-10
    mp3_vbr: bool = True                  # True=VBR(V0) / False=CBR
    dither: bool = True

    def with_(self, **kwargs) -> "EncodeOptions":
        return replace(self, **kwargs)


# --------------------------------------------------------------------------- #
# 格式定义
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AudioFormat:
    key: str
    label: str                      # 给用户看的名字
    ext: str                        # 不带点
    lossless: bool
    codec_family: tuple[str, ...]   # 源 codec 命中即可免重编码
    supports_cover: bool
    supports_tags: bool
    comment: str = ""
    fixed_sample_rate: int | None = None   # 强制采样率（Opus 恒 48k）
    allowed_sample_rates: tuple[int, ...] = ()


WAV = AudioFormat(
    key="wav",
    label="WAV",
    ext="wav",
    lossless=True,
    codec_family=("pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_u8"),
    supports_cover=False,
    supports_tags=False,
    comment="无压缩 PCM，体积最大；给剪辑软件和通用播放器最稳。",
)

FLAC = AudioFormat(
    key="flac",
    label="FLAC",
    ext="flac",
    lossless=True,
    codec_family=("flac",),
    supports_cover=True,
    supports_tags=True,
    comment="无损压缩，归档首选；保留封面和内嵌标签。",
)

AIFF = AudioFormat(
    key="aiff",
    label="AIFF / AIF",
    ext="aiff",
    lossless=True,
    codec_family=("pcm_s16be", "pcm_s24be", "pcm_s32be"),
    supports_cover=False,
    supports_tags=False,
    comment="大端 PCM，老 Mac / Pro Tools 生态。",
)

ALAC = AudioFormat(
    key="alac",
    label="ALAC",
    ext="m4a",
    lossless=True,
    codec_family=("alac",),
    supports_cover=True,
    supports_tags=True,
    comment="苹果无损，iPhone / iTunes / Apple Music 直接吃。",
)

BWF = AudioFormat(
    key="bwf",
    label="BWF",
    ext="wav",
    lossless=True,
    codec_family=("pcm_s16le", "pcm_s24le", "pcm_s32le"),
    supports_cover=False,
    supports_tags=False,
    comment="广播级 WAV，带 bext 块（后期/电台过审用）。",
)

MP3 = AudioFormat(
    key="mp3",
    label="MP3",
    ext="mp3",
    lossless=False,
    codec_family=("mp3",),
    supports_cover=True,
    supports_tags=True,
    comment="兼容性之王；默认 V0 可变码率，约 245kbps。",
)

OGG = AudioFormat(
    key="ogg",
    label="OGG Vorbis",
    ext="ogg",
    lossless=False,
    codec_family=("vorbis",),
    # ffmpeg 的 ogg 封装器不接受 attached_pic，塞封面会直接报
    # "Unsupported codec id in stream 1"
    supports_cover=False,
    supports_tags=True,
    comment="同码率下通常比 MP3 好听，Android / 开源播放器友好。",
)

AAC = AudioFormat(
    key="aac",
    label="AAC",
    ext="m4a",
    lossless=False,
    codec_family=("aac",),
    supports_cover=True,
    supports_tags=True,
    comment="手机 / 耳机 / 车载通用；源本身是 AAC 时直接封装不重压。",
)

OPUS = AudioFormat(
    key="opus",
    label="Opus",
    ext="opus",
    lossless=False,
    codec_family=("opus",),
    # 同上：ogg 封装器不支持附加图片
    supports_cover=False,
    supports_tags=True,
    comment="低码率最强；内部恒 48kHz，适合手机省空间。",
    fixed_sample_rate=48000,
)

AC3 = AudioFormat(
    key="ac3",
    label="AC3",
    ext="ac3",
    lossless=False,
    codec_family=("ac3",),
    supports_cover=False,
    supports_tags=False,
    comment="家庭影院杜比数字；上限 640k，且不支持封面和标签。",
    allowed_sample_rates=(32000, 44100, 48000),
)

#: 顺序即 GUI 下拉框顺序，按用户给的清单排列
ALL: tuple[AudioFormat, ...] = (
    WAV, FLAC, AIFF, ALAC, BWF, MP3, OGG, AAC, OPUS, AC3,
)

BY_KEY = {f.key: f for f in ALL}
BY_EXT = {}
for _f in ALL:
    BY_EXT.setdefault(_f.ext, []).append(_f)


def get(key: str) -> AudioFormat:
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(f"未知输出格式：{key}（可选：{', '.join(BY_KEY)}）") from None


# --------------------------------------------------------------------------- #
# 是否可以直接 copy
# --------------------------------------------------------------------------- #


def can_copy(
    fmt: AudioFormat,
    src_codec: str,
    src_sample_rate: int,
    src_bits: int,
    opts: EncodeOptions,
) -> bool:
    """源编码是否与目标一致、且用户没要求改变参数。"""
    if src_codec not in fmt.codec_family:
        return False
    if opts.sample_rate and opts.sample_rate != src_sample_rate:
        return False
    # 用户点名要某个码率 → 必须真的重编码
    if opts.bitrate_kbps and not fmt.lossless:
        return False
    # 无损目标：要求了不同位深就得重编码
    if fmt.lossless and opts.bit_depth and opts.bit_depth != src_bits:
        return False
    return True


# --------------------------------------------------------------------------- #
# 参数生成
# --------------------------------------------------------------------------- #


def _pcm_bits(opts: EncodeOptions, src_bits: int) -> int:
    """无损目标的位深：用户指定优先，否则跟随源（>16bit 保 24）。"""
    if opts.bit_depth:
        return 24 if opts.bit_depth > 16 else 16
    return 24 if (src_bits or 0) > 16 else 16


def _dither(opts: EncodeOptions, src_bits: int, out_bits: int) -> list[str]:
    """24 → 16 的降位深必须抖动，否则量化失真可闻。"""
    if not opts.dither or (src_bits or 0) <= out_bits:
        return []
    return ["-af", f"aresample=osf=s{out_bits}:dither_method=triangular_hp"]


def _sample_rate_args(fmt: AudioFormat, opts: EncodeOptions, src_sr: int) -> list[str]:
    rate = opts.sample_rate or fmt.fixed_sample_rate
    if fmt.allowed_sample_rates:
        if rate and rate not in fmt.allowed_sample_rates:
            rate = 48000
        elif not rate and src_sr not in fmt.allowed_sample_rates:
            rate = 48000
    if rate and rate != src_sr:
        return ["-ar", str(rate)]
    if rate and fmt.fixed_sample_rate:
        return ["-ar", str(rate)]
    return []


def build_args(
    fmt: AudioFormat,
    opts: EncodeOptions,
    *,
    src_codec: str,
    src_sample_rate: int,
    src_bits: int,
    copy: bool,
) -> list[str]:
    """生成 ffmpeg 的编码参数（不含输入/输出路径）。"""
    if copy:
        return ["-c:a", "copy"]

    rate_args = _sample_rate_args(fmt, opts, src_sample_rate)
    k = fmt.key

    # ---- 无损 / PCM 系 ----
    if k in ("wav", "aiff", "bwf"):
        bits = _pcm_bits(opts, src_bits)
        endian = "be" if k == "aiff" else "le"
        args = ["-c:a", f"pcm_s{bits}{endian}", *rate_args, *_dither(opts, src_bits, bits)]
        if k == "aiff":
            args += ["-f", "aiff"]
        else:
            args += ["-f", "wav"]
            if k == "bwf":
                # bext 块 + 广播级容器
                args += ["-write_bext", "1", "-rf64", "auto"]
        return args

    if k == "flac":
        bits = _pcm_bits(opts, src_bits)
        level = 8 if opts.compression_level is None else max(0, min(12, opts.compression_level))
        args = [
            "-c:a", "flac",
            "-compression_level", str(level),
            "-sample_fmt", "s32" if bits > 16 else "s16",
            *rate_args,
            *_dither(opts, src_bits, bits),
            "-f", "flac",
        ]
        return args

    if k == "alac":
        bits = _pcm_bits(opts, src_bits)
        return [
            "-c:a", "alac",
            "-sample_fmt", "s32p" if bits > 16 else "s16p",
            *rate_args,
            *_dither(opts, src_bits, bits),
            "-f", "ipod",
        ]

    # ---- 有损 ----
    if k == "mp3":
        if opts.bitrate_kbps:
            args = ["-c:a", "libmp3lame", "-b:a", f"{int(opts.bitrate_kbps)}k"]
        elif opts.mp3_vbr:
            args = ["-c:a", "libmp3lame", "-q:a", "0"]
        else:
            args = ["-c:a", "libmp3lame", "-b:a", "320k"]
        return [*args, *rate_args, "-id3v2_version", "3", "-f", "mp3"]

    if k == "ogg":
        q = 8 if opts.vorbis_quality is None else max(0, min(10, opts.vorbis_quality))
        return ["-c:a", "libvorbis", "-q:a", str(q), *rate_args, "-f", "ogg"]

    if k == "aac":
        br = int(opts.bitrate_kbps or 256)
        return ["-c:a", "aac", "-b:a", f"{br}k", *rate_args, "-f", "ipod"]

    if k == "opus":
        br = int(opts.bitrate_kbps or 160)
        return [
            "-c:a", "libopus", "-b:a", f"{br}k",
            "-ar", str(fmt.fixed_sample_rate or 48000),
            "-f", "opus",
        ]

    if k == "ac3":
        br = min(int(opts.bitrate_kbps or 640), 640)
        return ["-c:a", "ac3", "-b:a", f"{br}k", *rate_args, "-f", "ac3"]

    raise ValueError(f"未实现的输出格式：{k}")


# --------------------------------------------------------------------------- #
# 映射 / 警告
# --------------------------------------------------------------------------- #


def default_for_source(codec: str) -> AudioFormat:
    """解密出来的明文该往哪个格式走（不给用户选时的默认）。"""
    return {
        "flac": FLAC,
        "vorbis": OGG,
        "mp3": MP3,
        "aac": AAC,
        "alac": ALAC,
        "opus": OPUS,
        "ac3": AC3,
        "pcm_s16le": WAV,
        "pcm_s24le": WAV,
    }.get(codec, FLAC)


def warnings_for(fmt: AudioFormat, src_codec: str) -> list[str]:
    """在真正编码前提醒用户他可能不想这么干。"""
    lossy_src = src_codec not in {
        "flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le",
        "pcm_f32le", "wavpack", "ape", "truehd",
    }
    out: list[str] = []

    if lossy_src and fmt.lossless:
        out.append(
            f"源音频是 {src_codec}（有损），转成 {fmt.label} 不会提升音质，"
            f"只会让文件变大。除非是为了做剪辑素材，否则建议保持原格式。"
        )
    if not fmt.supports_cover:
        out.append(f"{fmt.label} 不支持内嵌封面，封面会被丢弃。")
    if not fmt.supports_tags:
        out.append(f"{fmt.label} 不支持完整音频标签（歌名/歌手），元数据会大部分丢失。")
    if fmt.key == "ac3":
        out.append("AC3 码率上限 640kbps，且只支持 32/44.1/48kHz 采样率。")
    if fmt.key == "opus":
        out.append("Opus 内部固定 48kHz，无论源码率多少都会被重采样。")
    return out
