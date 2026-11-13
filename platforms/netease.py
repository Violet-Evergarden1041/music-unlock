"""网易云音乐 ncm 解密。

格式（偏移都是小端）
--------------------
```
0x00  8    "CTENFDAM" 魔数
0x08  2    跳过
0x0A  4    keyLen
      4*   keyCipher   （每字节 ^ 0x64，再用 CORE_KEY 做 AES-128-ECB）
      ...            → 明文去头 17 字节，得到 keyData
     4    metaLen
     4*   metaCipher  （每字节 ^ 0x63，base64 后去头 22 字节，再用 META_KEY 解密）
     4    CRC
     1    跳过
     4    imgLen
     4*   封面图（JPEG/PNG 原图）
     4    跳过
     4*   音频本体（与 256 字节 keybox 循环异或）
```

音频本体是原封不动的 mp3 / flac / m4a，解密后不需要转容器，
所以「保留原格式」这一档对 ncm 是真正的零损失。

算法移植自 unlock-music 的 ``src/decrypt/ncm.ts``（MIT, Copyright (c) 2019-2023 MengYX）。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from . import _aes
from .base import Decoded, DecryptError, register
from .qmc_algo import sniff_container_strict

MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687a4852416d736f356b496e62617857")
META_KEY = bytes.fromhex("2331346c6a6b5f215c5d2630553c2728")

#: ncm 里 format 字段可能缺，补一张兜底表
_FORMAT_FALLBACK = {"mp3": "mp3", "flac": "flac", "m4a": "m4a", "aac": "aac", "wav": "wav"}


_JPEG_SOI = b"\xff\xd8\xff"
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def normalize_cover(raw: bytes) -> bytes:
    """剥掉封面图前面可能存在的长度前缀。

    真实文件里封面块的开头是 4 字节小端长度，之后才是 JPEG/PNG 本体
    （实测：``eb 42 0a 00`` 后面紧跟 ``ff d8 ff e0``）。不剥掉的话，
    上层按魔数判断图片类型会失败，ffmpeg 也就读不了。
    """
    if not raw:
        return b""
    if raw.startswith(_JPEG_SOI) or raw.startswith(_PNG_SIG):
        return raw
    for skip in range(1, 17):
        if raw[skip:].startswith(_JPEG_SOI) or raw[skip:].startswith(_PNG_SIG):
            return raw[skip:]
    return raw


class NcmDecoder:
    def __init__(self, data: bytes) -> None:
        self.raw = data
        self.size = len(data)
        self.offset = 0
        if self.size < 16 or data[:8] != MAGIC:
            raise DecryptError("不是有效的 ncm 文件（魔数不匹配）")
        self.offset = 10

    # -- 内部工具 -------------------------------------------------------- #

    def _u32(self, offset: int) -> int:
        if offset + 4 > self.size:
            raise DecryptError("ncm 文件被截断")
        return int.from_bytes(self.raw[offset:offset + 4], "little")

    def _slice(self, start: int, length: int) -> bytes:
        if length < 0 or start + length > self.size:
            raise DecryptError("ncm 文件被截断")
        return self.raw[start:start + length]

    # -- 三段解析 -------------------------------------------------------- #

    def _key_data(self) -> bytes:
        key_len = self._u32(self.offset)
        self.offset += 4
        cipher = bytes(b ^ 0x64 for b in self._slice(self.offset, key_len))
        self.offset += key_len
        try:
            plain = _aes.decrypt_ecb(cipher, CORE_KEY)
        except ValueError as exc:
            raise DecryptError(f"密钥段解密失败：{exc}") from exc
        if len(plain) <= 17:
            raise DecryptError("密钥段长度异常")
        return plain[17:]

    @staticmethod
    def _key_box(key_data: bytes) -> bytes:
        box = bytearray(range(256))
        length = len(key_data) or 1
        j = 0
        for i in range(256):
            j = (box[i] + j + key_data[i % length]) & 0xFF
            box[i], box[j] = box[j], box[i]

        key_box = bytearray(256)
        for i in range(256):
            k = (i + 1) & 0xFF
            si = box[k]
            sj = box[(k + si) & 0xFF]
            key_box[i] = box[(si + sj) & 0xFF]
        return bytes(key_box)

    def _meta_data(self) -> dict:
        meta_len = self._u32(self.offset)
        self.offset += 4
        if meta_len == 0:
            return {}

        cipher = bytes(b ^ 0x63 for b in self._slice(self.offset, meta_len))
        self.offset += meta_len
        if len(cipher) <= 22:
            return {}

        try:
            encoded = cipher[22:].decode("utf-8", "ignore").strip()
            padding = (-len(encoded)) % 4
            decoded = base64.b64decode(encoded + "=" * padding)
            plain = _aes.decrypt_ecb(decoded, META_KEY)
            text = plain.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            # 元数据坏了不该葬送整首歌，音频照样能解
            return {}

        index = text.find(":")
        if index < 0:
            return {}
        try:
            payload = json.loads(text[index + 1:])
        except json.JSONDecodeError:
            return {}
        if text[:index] == "dj" and isinstance(payload, dict):
            payload = payload.get("mainMusic") or {}
        return payload if isinstance(payload, dict) else {}

    def _cover(self) -> bytes | None:
        """封面图夹在元数据之后，解不出来就算了。"""
        if self.offset + 9 > self.size:
            return None
        image_len = self._u32(self.offset + 5)
        start = self.offset + 9
        if image_len <= 0 or start + image_len > self.size:
            return None
        return normalize_cover(self.raw[start:start + image_len])

    # -- 主流程 ---------------------------------------------------------- #

    def decrypt(self) -> tuple[bytes, dict, bytes | None]:
        key_box = self._key_box(self._key_data())
        meta = self._meta_data()
        cover = self._cover()

        # 跳过 CRC(4) + 1 + imgLen(4) + 图片 + 4
        image_len = self._u32(self.offset + 5) if self.offset + 9 <= self.size else 0
        self.offset += image_len + 13
        if self.offset >= self.size:
            raise DecryptError("ncm 里没有音频数据")

        audio = bytes(
            byte ^ key_box[i & 0xFF]
            for i, byte in enumerate(self.raw[self.offset:])
        )
        return audio, meta, cover


class NeteaseDecoder:
    name = "netease"
    extensions = ("ncm",)

    def matches(self, path: Path) -> bool:
        return path.suffix.lower() == ".ncm"

    def guess_container(self, path: Path) -> str | None:
        # ncm 里可能是 mp3 / flac / m4a，不看文件内容猜不准
        return None

    def decode(self, path: Path) -> Decoded:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DecryptError(f"读不了文件：{exc}") from exc

        audio, meta, cover = NcmDecoder(data).decrypt()

        declared = str(meta.get("format") or "").lower()
        container = sniff_container_strict(audio)
        if container is None:
            container = _FORMAT_FALLBACK.get(declared)
        if container is None:
            raise DecryptError("ncm 解出来的数据不是已知音频格式，密钥或文件可能有问题")

        artists = [
            str(item[0])
            for item in (meta.get("artist") or [])
            if isinstance(item, (list, tuple)) and item
        ]
        out_meta: dict[str, str] = {}
        if meta.get("musicName"):
            out_meta["title"] = str(meta["musicName"])
        if artists:
            out_meta["artist"] = " / ".join(artists)
        if meta.get("album"):
            out_meta["album"] = str(meta["album"])

        notes: list[str] = []
        if not out_meta:
            notes.append("ncm 元数据解析失败，歌名/歌手将从文件名推断")

        return Decoded(
            data=audio,
            container=container,
            meta=out_meta,
            notes=notes,
            cover=cover,
        )


register(NeteaseDecoder())
