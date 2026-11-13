"""解密器插件的统一接口。

每个平台一个模块，实现 Decoder 协议并注册到 REGISTRY。
上层（pipeline / GUI）只认接口，不认识具体平台。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class DecryptError(Exception):
    """解密失败（密钥不对、文件损坏、格式不认识……）。"""


class NeedsClientError(DecryptError):
    """这个文件不带密钥，必须借助客户端才能解（例如 QQ 音乐 STag）。"""


@dataclass
class Decoded:
    """解密产物：明文音频 + 解析出的元数据。"""

    data: bytes
    container: str                    # flac / ogg / mp3 / m4a / wav / ape / wma
    meta: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    cover: bytes | None = None


@runtime_checkable
class Decoder(Protocol):
    name: str
    extensions: tuple[str, ...]

    #: 文件名里「歌名 - 歌手」还是「歌手 - 歌名」。
    #: QQ 音乐下载的是前者（``歌名 - 歌手``），酷狗 / 酷我是后者。
    filename_order: str = "title-artist"

    def matches(self, path: Path) -> bool:
        """扩展名没有代表性时可以再看文件头。"""

    def decode(self, path: Path) -> Decoded:
        ...


REGISTRY: list[Decoder] = []


def register(decoder: Decoder) -> Decoder:
    REGISTRY.append(decoder)
    return decoder


def decoder_for(path: str | Path) -> Decoder | None:
    path = Path(path)
    suffix = path.suffix.lower().lstrip(".")
    for decoder in REGISTRY:
        if suffix in decoder.extensions and decoder.matches(path):
            return decoder
    return None


def is_encrypted(path: str | Path) -> bool:
    return decoder_for(path) is not None


def guess_container(path: str | Path) -> str | None:
    """不解密也猜一下明文容器，用于提前提示音质。

    插件可以选择不实现 ``guess_container``，那就返回 None（未知即不提示）。
    """
    decoder = decoder_for(path)
    if decoder is not None:
        guess = getattr(decoder, "guess_container", None)
        if callable(guess):
            result = guess(path)
            if result:
                return result
        return None
    # 普通音频：后缀本身就是容器
    suffix = Path(path).suffix.lower().lstrip(".")
    return suffix if suffix in PLAIN_AUDIO_EXTS else None


def filename_order(path: str | Path) -> str:
    """这个平台的文件名是「歌名 - 歌手」还是「歌手 - 歌名」。"""
    decoder = decoder_for(path)
    return getattr(decoder, "filename_order", "title-artist") if decoder else "title-artist"


#: 已经是普通音频的后缀——不需要解密，直接进转码
PLAIN_AUDIO_EXTS = frozenset({
    "flac", "mp3", "m4a", "aac", "wav", "ogg", "oga", "opus",
    "aiff", "aif", "ape", "wma", "alac", "wv", "ac3", "mka", "dff", "dsf",
})


def is_plain_audio(path: str | Path) -> bool:
    """这个文件是不是普通音频（不需要解密）。"""
    return Path(path).suffix.lower().lstrip(".") in PLAIN_AUDIO_EXTS


def addable_extensions() -> tuple[str, ...]:
    """界面/命令行允许添加的全部后缀：加密格式 + 普通音频。"""
    return tuple(sorted(set(known_extensions()) | PLAIN_AUDIO_EXTS))


def known_extensions() -> tuple[str, ...]:
    exts: list[str] = []
    for decoder in REGISTRY:
        exts.extend(decoder.extensions)
    return tuple(sorted(set(exts)))


def _load_builtin() -> None:
    """导入内置平台模块，触发它们各自的 register()。"""
    from . import (  # noqa: F401
        kugou,
        kuwo,
        netease,
        qqmusic,
    )


_load_builtin()
