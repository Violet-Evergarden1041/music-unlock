"""酷我音乐 kwm 解密。

格式来自 unlock-music 的 ``src/decrypt/kwm.ts``：

```
0x00  16   "yeelion-kuwo-tme" 或 "yeelion-kuwo\\0\\0\\0\\0"
0x18   8   文件密钥（小端整数）
0x400  ..  音频本体，与 32 字节 mask 循环异或
```

mask 的生成有点绕：把那 8 字节按小端读成整数，转成十进制字符串，裁剪/补齐到
32 个字符，再和固定串 ``MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk`` 逐字符异或。
"""

from __future__ import annotations

from pathlib import Path

from .base import Decoded, DecryptError, register
from .qmc_algo import sniff_container_strict

MAGIC_TME = b"yeelion-kuwo-tme"
MAGIC_PLAIN = b"yeelion-kuwo\x00\x00\x00\x00"
MAGIC_LEN = 16
KEY_OFFSET = 0x18
KEY_LEN = 8
AUDIO_OFFSET = 0x400
MASK_LEN = 32
PREDEFINED_KEY = "MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk"


def _trim_key(raw: str) -> str:
    """裁到 32 个字符；不够就用自身反复补齐（对应 JS 的 padEnd）。"""
    if len(raw) > MASK_LEN:
        return raw[:MASK_LEN]
    if len(raw) < MASK_LEN:
        repeat = raw * (MASK_LEN // max(len(raw), 1) + 1)
        return repeat[:MASK_LEN]
    return raw


def create_mask(key_bytes: bytes) -> bytes:
    if len(key_bytes) != KEY_LEN:
        raise ValueError("kwm 密钥必须是 8 字节")
    key_str = str(int.from_bytes(key_bytes, "little"))
    trimmed = _trim_key(key_str)
    return bytes(
        ord(PREDEFINED_KEY[i]) ^ ord(trimmed[i]) for i in range(MASK_LEN)
    )


def _xor_with_mask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    stream = (mask * (n // MASK_LEN + 1))[:n]
    return (
        int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")
    ).to_bytes(n, "big")


class KuwoDecoder:
    #: 文件名是「歌手 - 歌名」
    filename_order = "artist-title"
    name = "kuwo"
    extensions = ("kwm",)

    def matches(self, path: Path) -> bool:
        return path.suffix.lower() == ".kwm"

    def guess_container(self, path: Path) -> str | None:
        return None

    def decode(self, path: Path) -> Decoded:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DecryptError(f"读不了文件：{exc}") from exc

        if len(data) < AUDIO_OFFSET:
            raise DecryptError("文件太小，不是有效的 kwm 文件")
        if not data.startswith((MAGIC_TME, MAGIC_PLAIN)):
            raise DecryptError("文件头不匹配，不是有效的 kwm 文件")

        mask = create_mask(data[KEY_OFFSET:KEY_OFFSET + KEY_LEN])
        plain = _xor_with_mask(data[AUDIO_OFFSET:], mask)

        container = sniff_container_strict(plain)
        if container is None:
            raise DecryptError(
                "解出来的数据不是已知音频格式，密钥可能不对。已中止。"
            )

        return Decoded(data=plain, container=container)


register(KuwoDecoder())
